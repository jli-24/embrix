from types import SimpleNamespace

import pytest

from embedded_copilot.desktop_workspace_projection.service import (
    DesktopWorkspaceProjectionService,
)


class Catalog:
    def get_project(self, project_id: str, *, tenant_id: str):
        return SimpleNamespace(fingerprint="sha256:project", model_dump=lambda mode=None: {"fingerprint": "sha256:project"})

    def get_project_for_requester(self, project_id: str, *, tenant_id: str, requester: str):
        return self.get_project(project_id, tenant_id=tenant_id)

    def context(self, project_id: str, *, tenant_id: str):
        return SimpleNamespace()

    def conversations(self, project_id: str, *, tenant_id: str, requester: str, cursor=None, limit=20):
        return SimpleNamespace(items=(), next_cursor=None)

    def workbench_resources(self, project_id: str, *, tenant_id: str, limit: int = 500):
        return (({"resource_id": "resource-1", "parent_id": None, "name": "main.c", "relative_path": "Core/main.c", "node_type": "FILE", "category": "FIRMWARE", "language": "C", "source_fingerprint": "sha256:file"},), False)


class Validation:
    def list_findings(self, tenant_id: str, project_id: str, *, limit: int):
        return (SimpleNamespace(model_dump=lambda mode=None: {"finding_id": "finding-1", "finding_type": "I2C_PULLUP_REQUIRED", "severity": "ERROR", "subject": "I2C 上拉缺失", "summary": "总线缺少上拉电阻", "evidence_ids": ["evidence-1"], "confidence": 0.96, "limitations": ["未执行实测"], "target_identity": "I2C1", "target_fingerprint": "sha256:target", "finding_fingerprint": "sha256:finding"}),)

    def list_proposals(self, tenant_id: str, project_id: str, *, limit: int):
        return (SimpleNamespace(status=SimpleNamespace(value="CREATED"), model_dump=lambda mode=None: {"proposal_id": "proposal-1", "proposal_type": "REPAIR", "subject": "增加 I2C 上拉", "summary": "补充 SCL/SDA 上拉", "rationale": "满足总线电气约束", "finding_ids": ["finding-1"], "evidence_ids": ["evidence-1"], "proposed_changes": [{"file_reference": "Hardware/controller.kicad_sch", "symbol": "R1", "logical_change": "增加 4.7kΩ 上拉"}], "risk": "LOW", "confidence": 0.9, "limitations": []}),)


def test_workbench_is_a_read_only_aggregate_without_absolute_paths():
    service = DesktopWorkspaceProjectionService(catalog=Catalog(), validation=Validation())
    result = service.workbench(tenant_id="tenant", requester_id="requester", project_id="project-1")
    assert result.schema_version == "workbench-v1"
    assert result.resources[0].relative_path == "Core/main.c"
    assert result.findings[0].finding_id == "finding-1"
    assert result.repair_alternatives[0].proposed_changes[0].file_reference == "Hardware/controller.kicad_sch"
    assert all("C:\\" not in node.relative_path and node.relative_path.startswith("/") is False for node in result.resources)


@pytest.mark.parametrize("limit", [100, 101, 500, 2000])
def test_workbench_caps_validation_page_without_reducing_resource_limit(limit: int):
    class RecordingCatalog(Catalog):
        resource_limit: int | None = None

        def workbench_resources(self, project_id: str, *, tenant_id: str, limit: int = 500):
            self.resource_limit = limit
            return (), False

    class BoundedValidation:
        requested_limits: list[int]

        def __init__(self) -> None:
            self.requested_limits = []

        def list_findings(self, tenant_id: str, project_id: str, *, limit: int):
            self.requested_limits.append(limit)
            if limit > 100:
                raise ValueError("list limit is invalid")
            return ()

        def list_proposals(self, tenant_id: str, project_id: str, *, limit: int):
            self.requested_limits.append(limit)
            if limit > 100:
                raise ValueError("list limit is invalid")
            return ()

    catalog = RecordingCatalog()
    validation = BoundedValidation()
    result = DesktopWorkspaceProjectionService(catalog=catalog, validation=validation).workbench(
        tenant_id="tenant", requester_id="requester", project_id="project-1", limit=limit,
    )
    assert result.resources == ()
    assert catalog.resource_limit == limit
    assert validation.requested_limits == [min(limit, 100), min(limit, 100)]
