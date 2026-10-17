from types import SimpleNamespace

import pytest

from embedded_copilot.desktop_workspace_projection import DesktopWorkspaceProjectionService


FP1 = "sha256:" + "1" * 64
FP2 = "sha256:" + "2" * 64


def _project(project_id: str, tenant_id: str):
    return SimpleNamespace(
        fingerprint=FP1,
        model_dump=lambda mode="json": {
            "project_id": project_id,
            "tenant_id": tenant_id,
            "name": "Project",
            "relative_path": "project",
            "initialization_status": "READY",
            "fingerprint": FP1,
        },
    )


class _Catalog:
    def __init__(self, *, page_incomplete: bool = False, circuit_revision: str = "r1", source_current: bool = True) -> None:
        self.page_incomplete = page_incomplete
        self.circuit_revision = circuit_revision
        self.source_current = source_current
        self.read_conversations: list[str] = []

    def get_project_for_requester(self, project_id: str, *, tenant_id: str, requester: str):
        if requester != "owner":
            raise PermissionError("not owner")
        return _project(project_id, tenant_id)

    def context(self, project_id: str, *, tenant_id: str):
        return SimpleNamespace(model_dump=lambda mode="python": {"groups": ()})

    def conversations(self, project_id: str, *, tenant_id: str, requester: str, cursor=None, limit=20):
        return SimpleNamespace(
            items=(SimpleNamespace(conversation_id="conversation-a"), SimpleNamespace(conversation_id="conversation-b")),
            next_cursor="2" if self.page_incomplete else None,
        )

    def conversation(self, project_id: str, conversation_id: str, *, tenant_id: str, requester: str):
        self.read_conversations.append(conversation_id)
        if conversation_id not in {"conversation-a", "conversation-b"}:
            raise LookupError("missing conversation")
        plan = SimpleNamespace(
            plan_id="plan-a",
            fingerprint=FP1,
            circuit_intent_id="circuit-a",
            circuit_intent_revision="r1",
            circuit_intent_fingerprint=FP1,
        )
        binding = SimpleNamespace(
            circuit_intent_id="circuit-a",
            circuit_intent_revision="r1",
            circuit_intent_fingerprint=FP1,
        )
        return SimpleNamespace(
            project_id=project_id,
            conversation_id=conversation_id,
            requirement_context=SimpleNamespace(fingerprint=FP1, effective_status="CONFIRMED"),
            architecture_design=SimpleNamespace(architecture_id="architecture-a", fingerprint=FP1, status="APPROVED"),
            schematic_planning=SimpleNamespace(status="APPROVED", approved_plan=plan),
            pcb_engineering_state=SimpleNamespace(fingerprint=FP1, revision="r1", status="CURRENT", intent_source_binding=binding),
            firmware_spec=None,
            turns=(),
        )

    def current_circuit_intents(self, *, tenant_id: str, requester: str, project_id: str):
        return (SimpleNamespace(
            object_id="circuit-a",
            revision=self.circuit_revision,
            fingerprint=FP1 if self.circuit_revision == "r1" else FP2,
            status="ACTIVE",
            provenance=SimpleNamespace(source_fingerprint=FP1),
        ),)

    def circuit_source_current(self, project_id: str, object_id: str, revision: str, *, tenant_id: str, requester: str) -> bool:
        assert (project_id, object_id, revision) == ("project-a", "circuit-a", self.circuit_revision)
        return self.source_current


def _project_facts(catalog: _Catalog, *, conversation_id: str | None = None):
    return DesktopWorkspaceProjectionService(catalog=catalog).project(
        tenant_id="tenant-a",
        requester_id="owner",
        project_id="project-a",
        conversation_id=conversation_id,
    ).authoritative_facts


def test_selected_conversation_reads_only_its_owner_and_current_circuit_binding() -> None:
    catalog = _Catalog()
    facts = _project_facts(catalog, conversation_id="conversation-b")
    assert catalog.read_conversations == ["conversation-b"]
    by_domain = {item.domain: item for item in facts}
    assert set(by_domain) == {"REQUIREMENT", "ARCHITECTURE", "CIRCUIT", "SCHEMATIC", "PCB", "FIRMWARE", "EVIDENCE"}
    assert by_domain["SCHEMATIC"].currentness == "CURRENT"
    assert by_domain["PCB"].currentness == "CURRENT"
    assert by_domain["FIRMWARE"].currentness == "UNAVAILABLE"
    assert by_domain["SCHEMATIC"].conversation_id == "conversation-b"
    assert by_domain["CIRCUIT"].owner == "ProjectCatalog"
    # Circuit is a project-owned fact even when a conversation is selected.
    assert by_domain["CIRCUIT"].conversation_id is None
    assert by_domain["CIRCUIT"].object_id == "circuit-a"


def test_circuit_revision_change_marks_only_explicit_dependents_stale() -> None:
    facts = _project_facts(_Catalog(circuit_revision="r2"), conversation_id="conversation-a")
    by_domain = {item.domain: item for item in facts}
    assert by_domain["SCHEMATIC"].currentness == "STALE"
    assert by_domain["SCHEMATIC"].reason_code == "CIRCUIT_REVISION_CHANGED"
    assert by_domain["PCB"].currentness == "STALE"
    assert by_domain["REQUIREMENT"].currentness == "CURRENT"
    assert by_domain["ARCHITECTURE"].currentness == "CURRENT"


def test_circuit_source_change_marks_owner_and_explicit_dependents_stale() -> None:
    facts = _project_facts(_Catalog(source_current=False), conversation_id="conversation-a")
    by_domain = {item.domain: item for item in facts}
    for domain in ("CIRCUIT", "SCHEMATIC", "PCB"):
        assert by_domain[domain].currentness == "STALE"
        assert by_domain[domain].reason_code == "CIRCUIT_SOURCE_CHANGED"
    assert by_domain["REQUIREMENT"].currentness == "CURRENT"


def test_unscanned_conversation_page_is_unknown_instead_of_missing() -> None:
    facts = _project_facts(_Catalog(page_incomplete=True))
    unknown = [item for item in facts if item.reason_code == "PAGE_NOT_SCANNED"]
    assert {item.domain for item in unknown} == {"REQUIREMENT", "ARCHITECTURE", "SCHEMATIC", "PCB", "FIRMWARE"}


def test_selected_conversation_cannot_be_inferred_from_project_page() -> None:
    with pytest.raises(LookupError):
        _project_facts(_Catalog(), conversation_id="conversation-elsewhere")
