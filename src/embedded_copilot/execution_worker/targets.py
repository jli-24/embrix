from __future__ import annotations

from .contracts import ExecutionTarget, TargetDiscoveryCandidate
from .store import SQLiteExecutionStore


class SQLiteTargetRegistry:
    """Separates discovery candidates from explicit, project-bound targets."""

    def __init__(self, store: SQLiteExecutionStore) -> None:
        self._store = store

    def bind(self, target: ExecutionTarget) -> ExecutionTarget:
        if target.binding_status.value != "BOUND":
            raise ValueError("target must be explicitly bound")
        candidate = self._store.get_candidate(target.tenant_id, target.project_id, target.candidate_id)
        if candidate is None or target.target_kind != candidate.target_kind:
            raise ValueError("target candidate binding mismatch")
        return self._store.save_target(target)

    def discover(self, candidate: TargetDiscoveryCandidate) -> TargetDiscoveryCandidate:
        """Persist observation only; discovery never creates trusted target state."""
        if self._store.is_project_tombstoned(candidate.tenant_id, candidate.project_id):
            raise ValueError("project is tombstoned")
        return self._store.save_candidate(candidate)

    def get_candidate(self, *, tenant_id: str, project_id: str, candidate_id: str) -> TargetDiscoveryCandidate | None:
        return self._store.get_candidate(tenant_id, project_id, candidate_id)

    def get_bound(self, *, tenant_id: str, project_id: str, target_id: str) -> ExecutionTarget | None:
        value = self._store.get_target(tenant_id, project_id, target_id)
        return value if value is not None and value.binding_status.value == "BOUND" else None

    def revalidate(self, target: ExecutionTarget) -> ExecutionTarget:
        current = self.get_bound(tenant_id=target.tenant_id, project_id=target.project_id, target_id=target.target_id)
        if current is None or current.fingerprint != target.fingerprint:
            raise ValueError("target identity changed")
        return current


__all__ = ("SQLiteTargetRegistry",)
