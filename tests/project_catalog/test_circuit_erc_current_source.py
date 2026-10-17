from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from embedded_copilot.engineering_copilot import (
    SchematicGenerationStatus,
    SchematicProductionStatus,
)
from embedded_copilot.project_catalog import ProjectCatalogForbidden
from embedded_copilot.project_catalog.exceptions import SchematicProductionRejected
from embedded_copilot.project_catalog.runtime import ProjectCatalogRuntime
from tests.application.test_operation_receipt_evidence_resolver import _source


def _current_source(tmp_path: Path):
    source, reference = _source()
    operation = next(value for value in source.operations.values() if hasattr(value, "source_operation_id"))
    receipt = next(value for value in source.receipts.values() if value.operation_id == reference.receipt_id)
    source_receipt = next(value for value in source.receipts.values() if value.operation_id == operation.source_operation_id)
    root = tmp_path / "project"
    target = root / operation.target_relative_path
    target.parent.mkdir(parents=True)
    target.write_bytes(b"current schematic")
    content_fp = "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest()
    source_receipt = source_receipt.model_copy(update={
        "workspace_mutations": (source_receipt.workspace_mutations[0].model_copy(
            update={"content_fingerprint": content_fp}
        ),),
    })
    operation = operation.model_copy(update={"schematic_artifact_fingerprint": content_fp})
    generation = SimpleNamespace(
        status=SchematicGenerationStatus.SUCCESS, validation_status="PARSE_VERIFIED",
        parse_validation=object(), artifact_path=operation.target_relative_path,
        artifact_fingerprint=content_fp,
        operation_id=operation.source_operation_id,
        operation_receipt_fingerprint=source_receipt.fingerprint,
    )
    production = SimpleNamespace(
        project_id=operation.project_id, conversation_id=operation.conversation_id,
        status=SchematicProductionStatus.GENERATED,
        operation_id=operation.source_operation_id,
        generation_receipt=generation,
        erc_receipt=SimpleNamespace(
            project_id=operation.project_id, conversation_id=operation.conversation_id,
            operation_id=operation.operation_id,
            operation_receipt_fingerprint=receipt.fingerprint,
            artifact_path=operation.target_relative_path,
            artifact_fingerprint=content_fp,
            engineering_status="PASS",
            result=receipt.schematic_erc_result,
        ),
    )

    class Catalog:
        _root = root
        _relative = ProjectCatalogRuntime._relative
        _current_schematic_bytes = ProjectCatalogRuntime._current_schematic_bytes
        validate_current_schematic_erc_source = ProjectCatalogRuntime.validate_current_schematic_erc_source
        circuit_source_current = ProjectCatalogRuntime.circuit_source_current

        def get_project_for_requester(self, project_id, *, tenant_id, requester):
            if requester != operation.requester_id:
                raise ProjectCatalogForbidden("owner mismatch")

        def conversation(self, project_id, conversation_id, *, tenant_id, requester):
            return SimpleNamespace(schematic_production=production)

        def knowledge_snapshot_root(self, project_id, *, tenant_id):
            return root

    return Catalog(), operation, receipt, source_receipt, target


def test_erc_source_current_bytes_owner_and_receipt_are_rechecked(tmp_path: Path) -> None:
    catalog, operation, receipt, source_receipt, target = _current_source(tmp_path)

    def check() -> None:
        catalog.validate_current_schematic_erc_source(
            operation=operation, receipt=receipt, source_receipt=source_receipt,
            tenant_id=operation.tenant_id, requester=operation.requester_id,
        )

    check()
    with pytest.raises(ProjectCatalogForbidden):
        catalog.validate_current_schematic_erc_source(
            operation=operation, receipt=receipt, source_receipt=source_receipt,
            tenant_id=operation.tenant_id, requester="other-user",
        )
    with pytest.raises(SchematicProductionRejected):
        catalog.validate_current_schematic_erc_source(
            operation=operation, receipt=receipt.model_copy(update={"fingerprint": "sha256:" + "f" * 64}),
            source_receipt=source_receipt, tenant_id=operation.tenant_id,
            requester=operation.requester_id,
        )
    target.write_bytes(b"changed schematic")
    with pytest.raises(SchematicProductionRejected, match="bytes changed"):
        check()
    target.unlink()
    with pytest.raises(SchematicProductionRejected, match="unavailable"):
        check()


def test_published_erc_source_becomes_stale_after_file_or_owner_change(tmp_path: Path) -> None:
    catalog, operation, _receipt, source_receipt, target = _current_source(tmp_path)
    _, reference = _source()
    content_fp = "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest()
    reference = reference.model_copy(update={
        "source": "KICAD_EXECUTION_WORKER",
        "artifact_fingerprint": content_fp,
        "source_fingerprint": content_fp,
    })
    production = catalog.conversation(
        operation.project_id, operation.conversation_id,
        tenant_id=operation.tenant_id, requester=operation.requester_id,
    ).schematic_production
    production.erc_receipt.result = SimpleNamespace(
        status="ERC_PASS", receipt_ref=source_receipt.fingerprint,
        schematic_artifact_ref=operation.target_relative_path,
        schematic_content_fingerprint=content_fp,
    )

    class Repository:
        mismatch = False

        def predesign_published_candidate(self, project_id, object_id, revision, *, tenant_id, requester):
            return None

        def circuit_source_current(self, project_id, object_id, revision, *, tenant_id, requester):
            return True

        def circuit_published_candidate(self, project_id, object_id, revision, *, tenant_id, requester):
            return SimpleNamespace(
                evidence_reference=reference, conversation_id=operation.conversation_id,
                project_id=project_id, tenant_id=tenant_id,
                object_id="other-circuit" if self.mismatch else object_id,
                proposal=SimpleNamespace(proposed_object=SimpleNamespace(revision=revision)),
                status=SimpleNamespace(value="APPLIED"),
            )

    catalog._repository = Repository()

    def current() -> bool:
        return catalog.circuit_source_current(
            operation.project_id, "circuit-1", "r1",
            tenant_id=operation.tenant_id, requester=operation.requester_id,
        )

    assert current()
    catalog._repository.mismatch = True
    assert not current()
    catalog._repository.mismatch = False
    target.write_bytes(b"modified after publication")
    assert not current()
    target.unlink()
    assert not current()
    target.write_bytes(b"current schematic")
    assert current()
    production.erc_receipt.operation_receipt_fingerprint = "sha256:" + "f" * 64
    assert not current()
