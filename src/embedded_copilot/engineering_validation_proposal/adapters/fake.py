from __future__ import annotations

import copy

from ..exceptions import ProposalConflict, ProposalRejected


class FakeProposalStore:
    """Offline-only store for unit tests; never used by production composition."""

    def __init__(self) -> None:
        self.tombstones: set[tuple[str, str]] = set()
        self.requests = {}
        self.results = {}
        self.findings = {}
        self.proposals = {}
        self.reviews = {}
        self.history = []
        self.audit = []

    def is_project_tombstoned(self, tenant_id, project_id):
        return (tenant_id, project_id) in self.tombstones

    def tombstone_project(self, tenant_id, project_id, tombstoned_at):
        del tombstoned_at
        self.tombstones.add((tenant_id, project_id))
        return True

    def _key(self, value):
        object_id = next(
            (
                getattr(value, field)
                for field in ("review_id", "finding_id", "proposal_id", "history_id", "event_id", "validation_id")
                if hasattr(value, field)
            ),
            "",
        )
        return value.tenant_id, value.project_id, object_id

    def _save(self, bucket, value, *, mutable=False):
        key = self._key(value)
        existing = bucket.get(key)
        if existing is not None and existing != value and not mutable:
            raise ProposalConflict("identity conflict")
        bucket[key] = copy.deepcopy(value)
        return copy.deepcopy(value)

    def _get(self, bucket, tenant_id, project_id, object_id):
        if self.is_project_tombstoned(tenant_id, project_id):
            return None
        for (tenant, project, current), value in bucket.items():
            if (tenant, project, current) == (tenant_id, project_id, object_id):
                return copy.deepcopy(value)
        return None

    def _list(self, bucket, tenant_id, project_id):
        if self.is_project_tombstoned(tenant_id, project_id):
            return ()
        return tuple(copy.deepcopy(value) for (tenant, project, _), value in sorted(bucket.items()) if (tenant, project) == (tenant_id, project_id))

    def save_request(self, value): return self._save(self.requests, value)
    def get_request(self, tenant_id, project_id, object_id): return self._get(self.requests, tenant_id, project_id, object_id)
    def list_requests(self, tenant_id, project_id): return self._list(self.requests, tenant_id, project_id)
    def save_result(self, value): return self._save(self.results, value)
    def get_result(self, tenant_id, project_id, object_id): return self._get(self.results, tenant_id, project_id, object_id)
    def save_finding(self, value): return self._save(self.findings, value)
    def get_finding(self, tenant_id, project_id, object_id): return self._get(self.findings, tenant_id, project_id, object_id)
    def list_findings(self, tenant_id, project_id): return self._list(self.findings, tenant_id, project_id)
    def save_proposal(self, value): return self._save(self.proposals, value, mutable=True)
    def get_proposal(self, tenant_id, project_id, object_id): return self._get(self.proposals, tenant_id, project_id, object_id)
    def list_proposals(self, tenant_id, project_id): return self._list(self.proposals, tenant_id, project_id)
    def save_review(self, value): return self._save(self.reviews, value, mutable=True)
    def get_review(self, tenant_id, project_id, object_id): return self._get(self.reviews, tenant_id, project_id, object_id)
    def list_reviews(self, tenant_id, project_id): return self._list(self.reviews, tenant_id, project_id)
    def append_history(self, value): self.history.append(copy.deepcopy(value)); return copy.deepcopy(value)
    def append_audit(self, value): self.audit.append(copy.deepcopy(value)); return copy.deepcopy(value)


__all__ = ("FakeProposalStore",)
