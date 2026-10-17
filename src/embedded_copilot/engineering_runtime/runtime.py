"""Engineering Object Runtime MVP composition root."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from embedded_copilot.engineering_runtime.context import (
    ContextRequest,
    EngineeringContextResolver,
    EngineeringContextSnapshot,
)
from embedded_copilot.engineering_runtime.lifecycle import (
    LifecycleActor,
    LifecycleManager,
)
from embedded_copilot.engineering_runtime.objects import (
    EngineeringObject,
    LifecycleStatus,
    ObjectRegistry,
    ObjectType,
)
from embedded_copilot.engineering_runtime.proposals import (
    ApprovalRecord,
    ObjectMutationRecord,
    ObjectProposal,
    ProposalStatus,
)
from embedded_copilot.engineering_runtime.relations import (
    Relation,
    RelationManager,
    RelationType,
)
from embedded_copilot.engineering_runtime.revision import RevisionManager


class EngineeringObjectRuntime:
    """Single in-memory runtime entry point for object access and mutation."""

    def __init__(self, *, project_id: str | None = None) -> None:
        self.project_id = project_id
        self.registry = ObjectRegistry()
        self.lifecycle = LifecycleManager()
        self.revisions = RevisionManager()
        self.relations = RelationManager(self.registry)
        self.context = EngineeringContextResolver(self.registry, self.relations)
        self._proposals: dict[str, ObjectProposal] = {}
        self._approvals: dict[str, ApprovalRecord] = {}
        self._mutation_records: dict[str, ObjectMutationRecord] = {}

    def _check_project(self, obj: EngineeringObject) -> None:
        if self.project_id is None:
            self.project_id = obj.project_id
        if obj.project_id != self.project_id:
            raise ValueError("object belongs to another project")

    def register_object(self, obj: EngineeringObject) -> EngineeringObject:
        self._check_project(obj)
        if obj.status != LifecycleStatus.DRAFT:
            raise ValueError("new objects must enter the runtime as DRAFT")
        return self.registry.register(obj)

    def get_object(
        self, object_id: str, revision: str | None = None
    ) -> EngineeringObject:
        return self.registry.get_object(object_id, revision)

    def list_revision(self, object_id: str) -> tuple[EngineeringObject, ...]:
        return self.registry.list_revision(object_id)

    def get_active_revision(self, object_id: str) -> EngineeringObject:
        return self.registry.get_active_revision(object_id)

    def transition_object(
        self,
        object_id: str,
        revision_or_target: str | LifecycleStatus,
        target_or_actor: LifecycleStatus | LifecycleActor,
        actor: LifecycleActor | None = None,
    ) -> EngineeringObject:
        if actor is None:
            revision = None
            target = revision_or_target
            lifecycle_actor = target_or_actor
        else:
            revision = revision_or_target
            target = target_or_actor
            lifecycle_actor = actor
        if not isinstance(target, LifecycleStatus) or not isinstance(
            lifecycle_actor, LifecycleActor
        ):
            raise TypeError(
                "transition_object expects target status and lifecycle actor"
            )
        obj = self.registry.get_object(
            object_id, revision if isinstance(revision, str) else None
        )
        changed = self.lifecycle.transition(obj, target, lifecycle_actor)
        return self.registry.replace(changed)

    def create_revision(
        self, object_id: str, changes: dict, provenance=None
    ) -> EngineeringObject:
        current = self.registry.get_active_revision(object_id)
        revision = self.revisions.create_revision(current, changes, provenance)
        return self.register_object(revision)

    def add_relation(
        self,
        source: EngineeringObject,
        relation_type: RelationType,
        target: EngineeringObject,
        evidence_refs: tuple[str, ...] = (),
    ) -> Relation:
        self._check_project(source)
        self._check_project(target)
        return self.relations.add_relation(source, relation_type, target, evidence_refs)

    def mark_stale(self, object_id: str) -> EngineeringObject:
        current = self.registry.get_active_revision(object_id)
        if current.status == LifecycleStatus.STALE:
            return current
        return self.transition_object(
            object_id, LifecycleStatus.STALE, LifecycleActor.DEPENDENCY_ENGINE
        )

    def propagate_stale(self, source_object_id: str) -> tuple[EngineeringObject, ...]:
        changed: list[EngineeringObject] = []
        pending = list(self.relations.downstream_object_ids(source_object_id))
        visited: set[str] = set()
        while pending:
            object_id = pending.pop(0)
            if object_id in visited:
                continue
            visited.add(object_id)
            current = self.registry.get_active_revision(object_id)
            if current.status == LifecycleStatus.ACTIVE:
                changed.append(self.mark_stale(object_id))
            pending.extend(self.relations.downstream_object_ids(object_id))
        return tuple(changed)

    def resolve_context(self, request: ContextRequest) -> EngineeringContextSnapshot:
        if self.project_id is not None and request.project_id != self.project_id:
            raise ValueError("context request belongs to another project")
        return self.context.resolve(request)

    def resolve_context_for_circuit(
        self,
        requirement_id: str,
        hardware_id: str,
        evidence_ids: tuple[str, ...] = (),
    ) -> EngineeringContextSnapshot:
        project_id = self.project_id
        if project_id is None:
            raise ValueError("runtime project is not established")
        return self.resolve_context(
            ContextRequest.create(
                project_id=project_id,
                target_object_type=ObjectType.CIRCUIT_INTENT,
                required_object_ids=(requirement_id, hardware_id),
                evidence_ids=evidence_ids,
            )
        )

    def create_proposal(
        self,
        proposed_object: EngineeringObject,
        *,
        context_fingerprint: str,
        proposal_id: str,
        proposer: str,
    ) -> ObjectProposal:
        self._check_project(proposed_object)
        if proposed_object.status != LifecycleStatus.DRAFT:
            raise ValueError("proposal object must be DRAFT")
        base = None
        if self.registry.contains(proposed_object.object_id):
            current = self.registry.get_active_revision(proposed_object.object_id)
            base = (current.revision, current.fingerprint)
        proposal = ObjectProposal.create(
            proposal_id=proposal_id,
            project_id=proposed_object.project_id,
            target_object_id=proposed_object.object_id,
            target_object_type=proposed_object.object_type,
            proposed_object=proposed_object,
            base_revision=base[0] if base else None,
            base_fingerprint=base[1] if base else None,
            context_fingerprint=context_fingerprint,
            proposer=proposer,
        )
        if proposal_id in self._proposals:
            raise ValueError("proposal already exists")
        self._proposals[proposal_id] = proposal
        return proposal

    def approve_proposal(
        self, proposal_id: str, *, approval_id: str, reviewer: str
    ) -> ApprovalRecord:
        proposal = self._proposals[proposal_id]
        if proposal.status != ProposalStatus.CREATED:
            raise ValueError("only CREATED proposals can be approved")
        approval = ApprovalRecord.approve(
            approval_id=approval_id,
            project_id=proposal.project_id,
            proposal_id=proposal.proposal_id,
            proposal_fingerprint=proposal.fingerprint,
            reviewer=reviewer,
            reviewed_at=datetime.now(UTC),
        )
        self._approvals[proposal_id] = approval
        self._proposals[proposal_id] = proposal.model_copy(
            update={"status": ProposalStatus.APPROVED}
        )
        return approval

    def apply_proposal(self, proposal_id: str) -> ObjectMutationRecord:
        proposal = self._proposals[proposal_id]
        approval = self._approvals.get(proposal_id)
        if proposal.status != ProposalStatus.APPROVED or approval is None:
            raise ValueError("proposal requires an approval before apply")
        if approval.proposal_fingerprint != proposal.fingerprint:
            raise ValueError("proposal fingerprint does not match approval")
        proposed = proposal.proposed_object
        current = (
            self.registry.get_active_revision(proposed.object_id)
            if self.registry.contains(proposed.object_id)
            else None
        )
        if current is not None and (current.revision, current.fingerprint) != (
            proposal.base_revision,
            proposal.base_fingerprint,
        ):
            raise ValueError("proposal base revision is stale")
        reviewed = self.lifecycle.transition(
            proposed, LifecycleStatus.REVIEW, LifecycleActor.RUNTIME
        )
        approved = self.lifecycle.transition(
            reviewed, LifecycleStatus.APPROVED, LifecycleActor.REVIEWER
        )
        active = self.lifecycle.transition(
            approved, LifecycleStatus.ACTIVE, LifecycleActor.RUNTIME
        )
        if current is not None:
            if current.status not in {LifecycleStatus.ACTIVE, LifecycleStatus.STALE}:
                raise ValueError("current object cannot be superseded")
            self.registry.replace(
                self.lifecycle.transition(
                    current, LifecycleStatus.SUPERSEDED, LifecycleActor.RUNTIME
                )
            )
        self.registry.store_revision(active)
        self.propagate_stale(active.object_id)
        apply_id = (
            "apply-"
            + hashlib.sha256(f"{proposal_id}|{active.revision}".encode()).hexdigest()[
                :24
            ]
        )
        record = ObjectMutationRecord.create(
            apply_id=apply_id,
            project_id=proposal.project_id,
            proposal_id=proposal_id,
            new_revision=active.revision,
            object_fingerprint=active.fingerprint,
            applied_at=datetime.now(UTC),
        )
        self._mutation_records[proposal_id] = record
        self._proposals[proposal_id] = proposal.model_copy(
            update={"status": ProposalStatus.APPLIED}
        )
        return record
