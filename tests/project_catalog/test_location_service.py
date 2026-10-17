from __future__ import annotations

from pathlib import Path

import pytest

from embedded_copilot.project_catalog import ProjectCatalogRejected, ProjectLocationService, ProjectLocationUnsafe


def make_service(tmp_path: Path, *, allow_list: tuple[Path, ...] = ()) -> tuple[ProjectLocationService, Path]:
    default_root = tmp_path / "default"
    default_root.mkdir()
    (tmp_path / "state").mkdir(exist_ok=True)
    service = ProjectLocationService(
        database=tmp_path / "state" / "desktop.sqlite3",
        default_root=default_root,
        metadata_directory=tmp_path / "state" / "projects",
        allow_list=allow_list,
    )
    return service, default_root


def test_default_location_is_tenant_visible_and_resolvable(tmp_path: Path) -> None:
    service, default_root = make_service(tmp_path)

    locations = service.list(tenant_id="desktop-tenant", requester="desktop-user")

    assert len(locations) == 1
    assert locations[0].location_id == "default-project-root"
    assert service.resolve(locations[0].location_id, tenant_id="desktop-tenant") == default_root


def test_register_location_requires_allow_list_and_records_audit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Keep the test workspace outside the simulated user home so the same
    # protected-directory rule used in production remains exercised.
    simulated_home = tmp_path / "home"
    simulated_home.mkdir()
    allowed = tmp_path / "stm32-projects"
    allowed.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: simulated_home))
    service, _ = make_service(tmp_path, allow_list=(allowed,))

    location = service.register(
        path=str(allowed),
        display_label="STM32 Projects",
        tenant_id="desktop-tenant",
        requester="desktop-user",
    )

    assert location.display_label == "STM32 Projects"
    assert service.resolve(location.location_id, tenant_id="desktop-tenant") == allowed
    audit = service.audit_status(location.location_id, tenant_id="desktop-tenant")
    assert audit["location_id"] == location.location_id
    assert audit["status"] == "AVAILABLE"
    assert audit["root_fingerprint"].startswith("sha256:")


def test_register_location_rejects_user_and_database_directories(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    simulated_home = tmp_path / "home"
    workspace = simulated_home / "workspace"
    workspace.mkdir(parents=True)
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: simulated_home))
    service, _ = make_service(tmp_path, allow_list=(workspace, state))

    with pytest.raises(ProjectCatalogRejected, match="user directory"):
        service.register(path=str(workspace), display_label=None, tenant_id="tenant", requester="user")
    with pytest.raises(ProjectCatalogRejected, match="database directory"):
        service.register(path=str(state), display_label=None, tenant_id="tenant", requester="user")


def test_register_location_rejects_symlink(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    simulated_home = tmp_path / "home"
    simulated_home.mkdir()
    target = tmp_path / "allowed"
    target.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are unavailable in this environment")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: simulated_home))
    service, _ = make_service(tmp_path, allow_list=(target, link))

    with pytest.raises(ProjectCatalogRejected, match="symbolic link"):
        service.register(path=str(link), display_label=None, tenant_id="tenant", requester="user")


def test_production_initialization_rebinds_stale_default_root(tmp_path: Path) -> None:
    old_root = tmp_path / "acceptance_projects"
    old_root.mkdir()
    (tmp_path / "state").mkdir()
    database = tmp_path / "state" / "desktop.sqlite3"
    metadata = tmp_path / "state" / "projects"
    ProjectLocationService(database=database, default_root=old_root, metadata_directory=metadata)

    new_root = tmp_path / "production-projects"
    new_root.mkdir()
    service = ProjectLocationService(
        database=database,
        default_root=new_root,
        metadata_directory=metadata,
        allow_list=(new_root,),
        purge_invalid_persisted=True,
    )

    assert service.resolve("default-project-root", tenant_id="desktop-tenant") == new_root
    import sqlite3

    with sqlite3.connect(database) as connection:
        roots = connection.execute("SELECT root_path FROM project_locations").fetchall()
        assert all("acceptance_projects" not in str(row[0]) for row in roots)


def test_default_root_collision_with_configured_root_is_idempotent(tmp_path: Path) -> None:
    old_root = tmp_path / "acceptance_projects"
    old_root.mkdir()
    configured_root = tmp_path / "production-projects"
    configured_root.mkdir()
    (tmp_path / "state").mkdir()
    database = tmp_path / "state" / "desktop.sqlite3"
    metadata = tmp_path / "state" / "projects"

    ProjectLocationService(
        database=database,
        default_root=old_root,
        metadata_directory=metadata,
        allow_list=(configured_root,),
    )
    ProjectLocationService(
        database=database,
        default_root=configured_root,
        metadata_directory=metadata,
        allow_list=(configured_root,),
        production_mode=True,
        purge_invalid_persisted=True,
    )
    ProjectLocationService(
        database=database,
        default_root=configured_root,
        metadata_directory=metadata,
        allow_list=(configured_root,),
        production_mode=True,
        purge_invalid_persisted=True,
    )

    import sqlite3

    fingerprint = ProjectLocationService._root_fingerprint(configured_root)
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            "SELECT location_id, source, status FROM project_locations WHERE tenant_id='*' AND root_fingerprint=?",
            (fingerprint,),
        ).fetchall()
    assert len(rows) == 1
    assert rows[0][1:] == ("PRODUCTION_CONFIG", "ACTIVE")


def test_native_picker_registration_is_exact_and_not_allow_list_bound(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    simulated_home = tmp_path / "home"
    simulated_home.mkdir()
    selected = tmp_path / "STM32Projects"
    selected.mkdir()
    sibling = tmp_path / "STM32ProjectsSibling"
    sibling.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: simulated_home))
    service, _ = make_service(tmp_path)

    location = service.register(path=str(selected), display_label=None, tenant_id="tenant", requester="user")

    assert service.resolve(location.location_id, tenant_id="tenant") == selected
    assert len(service.list(tenant_id="tenant", requester="user")) == 2
    assert all(item.path_leaf != sibling.name for item in service.list(tenant_id="tenant", requester="user"))


def test_native_picker_registration_persists_and_revoked_root_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    simulated_home = tmp_path / "home"
    simulated_home.mkdir()
    selected = tmp_path / "STM32Projects"
    selected.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: simulated_home))
    service, _ = make_service(tmp_path)
    location = service.register(path=str(selected), display_label="STM32 Projects", tenant_id="tenant", requester="user")

    reopened = ProjectLocationService(
        database=tmp_path / "state" / "desktop.sqlite3",
        default_root=None,
        metadata_directory=tmp_path / "state" / "projects",
        production_mode=True,
        purge_invalid_persisted=True,
    )
    assert any(item.location_id == location.location_id for item in reopened.list(tenant_id="tenant", requester="user"))
    reopened.revoke(location.location_id, tenant_id="tenant", requester="user")
    with pytest.raises(ProjectCatalogRejected, match="not found"):
        reopened.resolve(location.location_id, tenant_id="tenant")


@pytest.mark.parametrize(
    "value",
    [r"\\server\share", r"\\server\share\project", r"\\?\C:\\projects", r"C:\\projects:stream", r"C:\\CON"],
)
def test_native_picker_registration_rejects_unc_device_ads_and_reserved_paths(tmp_path: Path, value: str, monkeypatch: pytest.MonkeyPatch) -> None:
    simulated_home = tmp_path / "home"
    simulated_home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: simulated_home))
    service, _ = make_service(tmp_path)

    with pytest.raises(ProjectCatalogRejected):
        service.register(path=value, display_label=None, tenant_id="tenant", requester="user")


def test_unc_path_is_rejected_before_filesystem_probe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    simulated_home = tmp_path / "home"
    simulated_home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: simulated_home))
    service, _ = make_service(tmp_path)
    import embedded_copilot.project_catalog.location_service as module

    def fail_probe(_value: Path) -> bool:
        raise AssertionError("UNC paths must be rejected before filesystem probing")

    monkeypatch.setattr(module.ProjectLocationService, "_contains_reparse_point", staticmethod(fail_probe))
    monkeypatch.setattr(Path, "is_symlink", fail_probe)
    monkeypatch.setattr(Path, "resolve", fail_probe)

    with pytest.raises(ProjectLocationUnsafe):
        service.register(path=r"\\server\share", display_label=None, tenant_id="tenant", requester="user")


def test_known_fixture_is_quarantined_by_explicit_identity_not_deleted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    simulated_home = tmp_path / "home"
    simulated_home.mkdir()
    fixture = tmp_path / "known-fixture"
    fixture.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: simulated_home))
    import embedded_copilot.project_catalog.location_service as module

    monkeypatch.setattr(module, "_KNOWN_FIXTURE_ROOTS", frozenset({module.canonical_root_identity(fixture)}))
    first, _ = make_service(tmp_path, allow_list=(fixture,))
    assert first.list(tenant_id="tenant", requester="user")
    database = tmp_path / "state" / "desktop.sqlite3"
    second = ProjectLocationService(
        database=database,
        default_root=fixture,
        metadata_directory=tmp_path / "state" / "projects",
        allow_list=(fixture,),
        production_mode=True,
        purge_invalid_persisted=True,
    )
    assert all(item.path_leaf != fixture.name for item in second.list(tenant_id="tenant", requester="user"))
    import sqlite3

    with sqlite3.connect(database) as connection:
        row = connection.execute("SELECT source, status FROM project_locations WHERE location_id='default-project-root'").fetchone()
    assert row == ("TEST_FIXTURE", "REVOKED")
