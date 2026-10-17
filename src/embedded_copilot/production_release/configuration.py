"""Safe production configuration composition and state-path derivation."""

from __future__ import annotations

import json
import ntpath
import os
from collections.abc import Mapping
from pathlib import Path

from pydantic_core import PydanticUndefined

from embedded_copilot.core.config import Settings

from .paths import ProductionStateLayout


class ProductionConfigurationRejected(ValueError):
    pass


_KNOWN_FIXTURE_ROOTS = frozenset({r"c:\tmp\embedded-copilot-native-picker-root"})
_KNOWN_FIXTURE_ANCHORS = frozenset({r"c:\acceptance_projects", r"c:\acceptance_state"})


_SAFE_KEYS = frozenset(
    {
        "project_root",
        "project_location_allow_list",
        "desktop_state_database",
        "desktop_project_metadata_directory",
        "ollama_base_url",
        "embedding_provider",
        "embedding_model",
        "request_timeout_seconds",
        "analysis_timeout_seconds",
        "desktop_session_ttl_seconds",
        "chat_provider",
        "chat_connection_name",
        "chat_model",
        "chat_last_test_status",
        "chat_tested_at",
        "chat_tested_configuration_fingerprint",
        "chat_credential_fingerprint",
        "chat_available_models",
        "chat_model_available",
        "chat_verified_model",
        "chat_response_model",
        "chat_response_id",
        "chat_latency_ms",
        "chat_http_status",
        "chat_protocol_adapter",
        # These fields are the non-secret provider projection persisted by
        # ProviderConfigurationStore.  The credential itself remains in the
        # credential boundary and is never accepted by this loader.
        "chat_base_url",
        "chat_endpoint_id",
        "chat_credential_ref",
        "chat_model_profile_id",
        "chat_verification_id",
        "chat_verification_status",
        "chat_verification_persisted",
        "chat_verification_persistence_error",
        "chat_provider_profiles",
    }
)


def _safe_json(path: Path | None) -> dict[str, object]:
    if path is None or not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ProductionConfigurationRejected("safe configuration is invalid") from error
    if not isinstance(value, dict) or not set(value).issubset(_SAFE_KEYS):
        raise ProductionConfigurationRejected("safe configuration contains an unsupported or secret field")
    return value


def load_safe_configuration(
    *,
    machine_file: Path | None,
    user_file: Path | None,
    environment: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Compose defaults < machine JSON < user JSON < explicit env override.

    Only explicitly whitelisted safe keys participate.  Secret names are never
    accepted by this loader, which keeps configuration files out of the
    credential boundary.
    """

    values = {**_safe_json(machine_file), **_safe_json(user_file)}
    source = environment if environment is not None else os.environ
    for key in _SAFE_KEYS:
        env_key = f"EMBEDDED_COPILOT_{key.upper()}"
        if env_key in source:
            values[key] = source[env_key]
    return values


def production_settings(settings: Settings) -> tuple[Settings, ProductionStateLayout]:
    """Derive canonical mutable paths without moving user project locations."""

    # Explicit database paths are used by offline production-composition tests
    # and controlled deployments. They must remain authoritative; only the
    # packaged default derives its state root from LocalAppData.
    default_database = Path(Settings.model_fields["desktop_state_database"].default)
    environment_database = os.environ.get("EMBEDDED_COPILOT_DESKTOP_STATE_DATABASE")
    # Pydantic settings may have populated this field from the legacy
    # environment used by earlier acceptance runs.  That value is not an
    # explicit v7 deployment override; only a caller-provided path different
    # from both the model default and the environment value is authoritative.
    explicit_database = (
        Path(settings.desktop_state_database) != default_database
        and (environment_database is None or Path(settings.desktop_state_database) != Path(environment_database))
    )
    if settings.production_state_root is None and explicit_database:
        layout = ProductionStateLayout.for_current_user(
            Path(settings.desktop_state_database).parent / ".v70-state"
        )
    else:
        layout = ProductionStateLayout.for_current_user(settings.production_state_root)
    layout.create()
    updates = {"desktop_local_identity_enabled": False}
    # Source-tree test/development composition may omit the explicit asset
    # path; use the repository's already-built production assets when present.
    # Packaged deployments still provide an explicit bundled path, and a
    # missing directory remains a fail-closed startup error in ``create_app``.
    if settings.desktop_static_assets is None:
        repository_dist = Path(__file__).resolve().parents[3] / "frontend" / "dist"
        if repository_dist.is_dir():
            updates["desktop_static_assets"] = repository_dist
    if not explicit_database:
        updates.update(
            {
                "desktop_state_database": layout.database / "desktop.sqlite3",
                "desktop_project_metadata_directory": layout.artifacts / "projects",
                "chroma_path": layout.chroma,
            }
        )
    derived = settings.model_copy(update=updates)
    return derived, layout


def _reject_acceptance_root(value: object) -> Path:
    candidate = Path(str(value)).expanduser()
    return candidate


def _root_identity(value: Path) -> str:
    return ntpath.normcase(ntpath.normpath(str(value).replace("/", "\\")))


def _is_known_fixture_root(value: Path) -> bool:
    identity = _root_identity(value)
    return identity in _KNOWN_FIXTURE_ROOTS or any(
        identity == anchor or identity.startswith(anchor + "\\")
        for anchor in _KNOWN_FIXTURE_ANCHORS
    )


def load_production_settings(*, assets: Path, layout: ProductionStateLayout) -> Settings:
    """Build production settings from the explicit per-user config only.

    Every Settings field is supplied explicitly, so Pydantic cannot read an
    ambient acceptance environment variable.  A missing root or allow-list is
    a fail-closed configuration error.
    """

    values = load_safe_configuration(
        machine_file=None,
        user_file=layout.production_config,
        environment={},
    )
    raw_root = values.get("project_root")
    raw_allow = values.get("project_location_allow_list")
    raw_database = values.get("desktop_state_database")
    raw_metadata_directory = values.get("desktop_project_metadata_directory")
    root = None if raw_root in (None, "") else _reject_acceptance_root(raw_root)
    if isinstance(raw_allow, str):
        allow_items = tuple(item.strip() for item in raw_allow.split(";") if item.strip())
    elif isinstance(raw_allow, (list, tuple)):
        allow_items = tuple(raw_allow)
    elif raw_allow in (None, ""):
        allow_items = ()
    else:
        raise ProductionConfigurationRejected("production project allow-list is invalid")
    allow_list = tuple(
        item
        for item in (_reject_acceptance_root(item) for item in allow_items)
        if not _is_known_fixture_root(item)
    )
    if root is not None and _is_known_fixture_root(root):
        # This exact path is a known fixture supplied by the acceptance
        # environment.  It is quarantined, not deleted by basename matching.
        root = None
    if root is not None and all(_root_identity(root) != _root_identity(item) for item in allow_list):
        allow_list = (root, *allow_list)

    defaults: dict[str, object] = {}
    for name, field in Settings.model_fields.items():
        if field.default is not PydanticUndefined:
            defaults[name] = field.default
    defaults.update(
        {
            "environment": "production",
            "desktop_static_assets": Path(assets),
            "production_state_root": layout.root,
            "project_root": root,
            "project_location_allow_list": allow_list,
        }
    )
    if raw_database not in (None, ""):
        defaults["desktop_state_database"] = _reject_acceptance_root(raw_database)
        # Let production_settings derive the migration/recovery layout from
        # this explicit catalog database instead of silently rebinding it to
        # the user's default AppData state root.
        defaults["production_state_root"] = None
    if raw_metadata_directory not in (None, ""):
        defaults["desktop_project_metadata_directory"] = _reject_acceptance_root(raw_metadata_directory)
    # BaseSettings parses environment values before applying explicit kwargs;
    # temporarily remove the product prefix so malformed acceptance values
    # cannot even be parsed during production composition. Restore verbatim.
    ambient = {key: value for key, value in os.environ.items() if key.startswith("EMBEDDED_COPILOT_")}
    try:
        for key in ambient:
            os.environ.pop(key, None)
        return Settings(_env_file=None, **defaults)
    finally:
        os.environ.update(ambient)


__all__ = (
    "ProductionConfigurationRejected",
    "load_production_settings",
    "load_safe_configuration",
    "production_settings",
)
