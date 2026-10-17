from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from embedded_copilot.api.main import create_app
from embedded_copilot.component_recommendation.catalog import (
    load_approved_component_source,
)
from embedded_copilot.core.config import Settings
from embedded_copilot.model_gateway.invocation.qualified_budget_producer import (
    QualifiedInvocationBudgetProducer,
)
from embedded_copilot.production_release.credentials import InMemoryCredentialProvider
from embedded_copilot.production_release.runtime import ProductionReleaseRuntime
from embedded_copilot.project_catalog import (
    ProjectCatalogConflict,
    ProjectCatalogForbidden,
    ProjectCatalogNotFound,
    ProjectCreationMode,
    ProjectCreationRequest,
    ProjectLocationReference,
    ProjectType,
)
from embedded_copilot.project_catalog.exceptions import ProjectCatalogUnavailable
from embedded_copilot.project_catalog.source_publication import (
    ComponentSourcePublishCommandV1,
    PublishedComponentSourcePort,
)
from embedded_copilot.project_catalog.source_publication_host import (
    CatalogSourcePublicationHost,
)
from tests.project_catalog.test_s05_model_candidate_runtime import _base_detail

TENANT = "tenant-assembly"
REQUESTER = "engineer-assembly"


def _default_runtime(tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "index.html").write_text("<html>desktop</html>", encoding="utf-8")
    projects = tmp_path / "projects"
    projects.mkdir()
    settings = Settings(
        environment="production",
        production_state_root=tmp_path / "state",
        project_root=projects,
        desktop_static_assets=assets,
        _env_file=None,
    )
    release = ProductionReleaseRuntime.create(
        settings, credentials=InMemoryCredentialProvider()
    )
    return settings, release, create_app(
        settings=release.settings,
        production_release_runtime=release,
    )


def _new_project(catalog, name):
    location = catalog.locations(tenant_id=TENANT, requester=REQUESTER)[0]
    preview = catalog.preview(
        ProjectCreationRequest(
            name=name,
            project_type=ProjectType.STM32,
            creation_mode=ProjectCreationMode.CREATE_NEW,
            location=ProjectLocationReference(
                location_id=location.location_id,
                fingerprint=location.fingerprint,
            ),
        ),
        tenant_id=TENANT,
        requester=REQUESTER,
    )
    project = catalog.create(
        preview.preview_id,
        preview.fingerprint,
        confirmed_at=datetime.now(UTC),
        tenant_id=TENANT,
        requester=REQUESTER,
    )
    conversation = catalog.create_conversation(
        project.project_id, tenant_id=TENANT, requester=REQUESTER
    ).conversation
    return project, _base_detail(conversation)


def _command(record, request_id, expected):
    return ComponentSourcePublishCommandV1(
        schema_version=1,
        request_id=request_id,
        source_id=record.source_id,
        expected_head_publication_fingerprint=expected,
        record=record,
    )


def test_default_app_assembles_offline_budget_and_empty_project_source(tmp_path):
    _settings, _release, app = _default_runtime(tmp_path)
    with TestClient(app) as client:
        catalog = app.state.project_catalog_runtime
        assert isinstance(app.state.source_publication_host, CatalogSourcePublicationHost)
        assert isinstance(
            app.state.model_invocation_service._budget_source,
            QualifiedInvocationBudgetProducer,
        )
        assert app.state.model_invocation_service._budget_source._sources == (
            None, None, None, None, None,
        )


        with catalog._repository._connect() as connection:
            before = connection.execute(
                "SELECT COUNT(*) FROM component_source_publications"
            ).fetchone()[0]
        assert before == 0
        assert client.get("/api/v1/health").status_code == 200
        with catalog._repository._connect() as connection:
            after = connection.execute(
                "SELECT COUNT(*) FROM component_source_publications"
            ).fetchone()[0]
        assert after == 0

        _project, detail = _new_project(catalog, "Unpublished project")
        assert app.state.hardware_component_source.records[0].mpn == "STM32F103C8T6"
        assert catalog.hardware_component_source_for(detail) is None
        assert app.state.source_publication_host.source_for(
            tenant_id=TENANT,
            project_id=detail.project_id,
            requester=REQUESTER,
        ) is None


def test_default_host_scopes_publications_replays_and_restores_heads(tmp_path):
    settings, _release, app = _default_runtime(tmp_path)
    record = load_approved_component_source().records[0]

    with TestClient(app):
        catalog = app.state.project_catalog_runtime
        host = app.state.source_publication_host
        project, detail = _new_project(catalog, "Published project")
        _other_project, other_detail = _new_project(catalog, "Separate project")

        command_a = _command(record, "assembly-a", None)

        with pytest.raises((ProjectCatalogNotFound, ProjectCatalogForbidden)):
            host.publish(
                tenant_id=TENANT,
                project_id=project.project_id,
                requester="different-owner",
                command=command_a,
            )

        first, first_is_current = host.publish(
            tenant_id=TENANT,
            project_id=project.project_id,
            requester=REQUESTER,
            command=command_a,
        )
        replay, replay_is_current = host.publish(
            tenant_id=TENANT,
            project_id=project.project_id,
            requester=REQUESTER,
            command=command_a,
        )
        assert first_is_current and replay_is_current
        assert replay == first
        with pytest.raises((ProjectCatalogNotFound, ProjectCatalogForbidden)):
            host.publish(
                tenant_id=TENANT,
                project_id=project.project_id,
                requester="different-owner",
                command=command_a,
            )

        with pytest.raises(ProjectCatalogConflict):
            host.publish(
                tenant_id=TENANT,
                project_id=project.project_id,
                requester=REQUESTER,
                command=_command(record, "assembly-stale-cas", None),
            )

        changed = type(record).create(
            **{
                **record.model_dump(mode="python", exclude={"fingerprint"}),
                "description": "Changed only inside isolated host assembly test",
            }
        )
        second, _ = host.publish(
            tenant_id=TENANT,
            project_id=project.project_id,
            requester=REQUESTER,
            command=_command(changed, "assembly-b", first.fingerprint),
        )
        third, _ = host.publish(
            tenant_id=TENANT,
            project_id=project.project_id,
            requester=REQUESTER,
            command=_command(record, "assembly-a-again", second.fingerprint),
        )
        assert (first.generation, second.generation, third.generation) == (1, 2, 3)
        assert third.record.fingerprint == first.record.fingerprint
        assert third.fingerprint != first.fingerprint
        assert isinstance(catalog.hardware_component_source_for(detail), PublishedComponentSourcePort)
        assert catalog.hardware_component_source_for(other_detail) is None

        with pytest.raises((ProjectCatalogNotFound, ProjectCatalogForbidden)):
            host.source_for(
                tenant_id="different-tenant",
                project_id=project.project_id,
                requester=REQUESTER,
            )

    reopened_release = ProductionReleaseRuntime.create(
        settings, credentials=InMemoryCredentialProvider()
    )
    reopened_app = create_app(
        settings=reopened_release.settings,
        production_release_runtime=reopened_release,
    )
    with TestClient(reopened_app) as client:
        reopened_catalog = reopened_app.state.project_catalog_runtime
        assert reopened_app.state.source_publication_host is not None
        assert isinstance(reopened_catalog.hardware_component_source_for(detail), PublishedComponentSourcePort)
        restored = reopened_app.state.source_publication_host.source_for(
            tenant_id=TENANT,
            project_id=detail.project_id,
            requester=REQUESTER,
        )
        assert isinstance(restored, PublishedComponentSourcePort)
        head = restored.current_publication(detail.project_id, record.source_id)
        assert head == third
        assert client.get("/api/v1/health").status_code == 200
        assert restored.current_publication(detail.project_id, record.source_id) == third
    import subprocess
    import sys

    script = """import sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[8])))
from tests.s02_offline_guard import pytest_configure, pytest_unconfigure
pytest_configure()
try:
    from fastapi.testclient import TestClient
    from embedded_copilot.api.main import create_app
    from embedded_copilot.core.config import Settings
    from embedded_copilot.production_release.credentials import InMemoryCredentialProvider
    from embedded_copilot.production_release.runtime import ProductionReleaseRuntime
    settings = Settings(environment="production",
        production_state_root=Path(sys.argv[1]), project_root=Path(sys.argv[2]),
        desktop_static_assets=Path(sys.argv[3]), _env_file=None)
    release = ProductionReleaseRuntime.create(settings,
        credentials=InMemoryCredentialProvider())
    app = create_app(settings=release.settings, production_release_runtime=release)
    with TestClient(app):
        host = app.state.source_publication_host
        port = host.source_for(tenant_id=sys.argv[4], project_id=sys.argv[5],
            requester=sys.argv[6])
        head = port.current_publication(sys.argv[5], sys.argv[7])
        print(head.fingerprint, head.generation)
finally:
    pytest_unconfigure()
"""
    process = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            script,
            str(tmp_path / "state"),
            str(tmp_path / "projects"),
            str(tmp_path / "assets"),
            TENANT,
            detail.project_id,
            REQUESTER,
            record.source_id,
            str(Path(__file__).resolve().parents[2] / "src"),
        ],
        capture_output=True,
        check=False,
        cwd=Path(__file__).resolve().parents[2],
        timeout=45,
    )
    output = process.stdout.decode("utf-8", errors="replace")
    error = process.stderr.decode("utf-8", errors="replace")
    assert process.returncode == 0, error
    assert output.splitlines()[-1] == f"{third.fingerprint} {third.generation}"


def test_corrupt_publication_head_fails_closed_without_static_fallback(tmp_path):
    _settings, _release, app = _default_runtime(tmp_path)
    record = load_approved_component_source().records[0]
    with TestClient(app):
        catalog = app.state.project_catalog_runtime
        host = app.state.source_publication_host
        project, detail = _new_project(catalog, "Corrupt head project")
        published, _ = host.publish(
            tenant_id=TENANT,
            project_id=project.project_id,
            requester=REQUESTER,
            command=_command(record, "assembly-corrupt-head", None),
        )
        assert published.generation == 1
        assert app.state.hardware_component_source is not None

        with catalog._repository._connect() as connection:
            connection.execute(
                "UPDATE component_source_heads SET publisher_id=? "
                "WHERE tenant_id=? AND project_id=? AND source_id=?",
                ("different-publisher", TENANT, project.project_id, record.source_id),
            )
        with pytest.raises(ProjectCatalogUnavailable):
            catalog.hardware_component_source_for(detail)
