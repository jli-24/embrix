"""Capability-first governance selection without provider execution."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence

from .contracts import canonical_fingerprint
from .governance import (
    AllowedModelPolicy,
    AutoSelectionEvidence,
    BoundModelIdentity,
    CandidateGroup,
    CandidateRejection,
    CandidateSelectionEvidence,
    CandidateSpec,
    EngineeringStage,
    ModelSelectionResult,
    ModelSelectionTaskProfile,
    PolicyScope,
    ProjectDefaultModelPolicy,
    SelectionMode,
    StageModelPolicy,
    default_engineering_stage_registry,
)
from .profiles import CapabilityStatus, ModelAvailability, ModelProfile


class ModelSelectionError(ValueError):
    """Stable, provider-safe governance rejection."""

    def __init__(self, code: str, *, insufficient: bool = False, selection: ModelSelectionResult | None = None) -> None:
        self.code = code
        self.insufficient = insufficient
        self.selection = selection
        super().__init__(code)


class ModelSelectionService:
    """Resolve governed fixed or priority-order group policy without provider execution.

    The service only performs identity and hard-gate checks.  It never scores,
    invokes a provider, or silently falls back to another model.
    """

    def __init__(
        self,
        *,
        models: Sequence[ModelProfile] = (),
        credential_ref_by_endpoint: Mapping[str, str] | None = None,
        available_credential_refs: Sequence[str] = (),
        endpoint_health: Mapping[str, str] | None = None,
        quota_available_by_endpoint: Mapping[str, bool] | None = None,
        strict_gates: bool = True,
    ) -> None:
        self._models = {item.model_profile_id: item for item in models}
        self._credential_ref_by_endpoint = dict(credential_ref_by_endpoint or {})
        self._available_credentials = set(available_credential_refs)
        self._endpoint_health = dict(endpoint_health or {})
        self._quota_available = dict(quota_available_by_endpoint or {})
        if type(strict_gates) is not bool:
            raise TypeError("strict_gates is invalid")
        self._strict_gates = strict_gates
        self._stages = {item.stage_id: item for item in default_engineering_stage_registry()}

    def select_model(
        self,
        task_profile: ModelSelectionTaskProfile,
        stage_policy: StageModelPolicy | None,
        allowed_models: Sequence[AllowedModelPolicy],
        *,
        request_override: BoundModelIdentity | None = None,
        project_policy: ProjectDefaultModelPolicy | None = None,
        candidate_group: CandidateGroup | None = None,
        candidate_eligibility: Callable[[BoundModelIdentity], None] | None = None,
    ) -> ModelSelectionResult:
        if type(task_profile) is not ModelSelectionTaskProfile:
            raise TypeError("task profile is invalid")
        if stage_policy is not None and stage_policy.project_id != task_profile.project_id:
            raise ModelSelectionError("STAGE_POLICY_BINDING_MISMATCH")
        if project_policy is not None and project_policy.project_id != task_profile.project_id:
            raise ModelSelectionError("PROJECT_POLICY_BINDING_MISMATCH")

        effective = self.effective_allowed(
            allowed_models,
            project_id=task_profile.project_id,
            stage_id=task_profile.stage,
        )
        effective_by_identity = {self._identity_key(item): item for item in effective}
        if request_override is not None:
            self._gate(request_override, task_profile)
            if self._identity_key(request_override) not in effective_by_identity:
                raise ModelSelectionError("MODEL_NOT_ALLOWED")
            if candidate_eligibility is not None:
                candidate_eligibility(request_override)
            return ModelSelectionResult(
                selected_model=request_override,
                selection_reason="REQUEST_OVERRIDE",
                fallback_candidates=self._fallbacks(effective_by_identity, request_override, task_profile),
            )

        policy: StageModelPolicy | ProjectDefaultModelPolicy | None = stage_policy or project_policy
        if policy is None:
            return self._auto_result(effective_by_identity, task_profile, "ALLOWED_POOL_ROUTER_INPUT")

        if policy.selection_mode is SelectionMode.FIXED_MODEL:
            assert policy.fixed_model is not None
            if self._identity_key(policy.fixed_model) not in effective_by_identity:
                raise ModelSelectionError("MODEL_NOT_ALLOWED")
            self._gate(policy.fixed_model, task_profile)
            if candidate_eligibility is not None:
                candidate_eligibility(policy.fixed_model)
            return ModelSelectionResult(
                selected_model=policy.fixed_model,
                selection_reason="STAGE_FIXED_MODEL" if isinstance(policy, StageModelPolicy) else "PROJECT_FIXED_MODEL",
                fallback_candidates=self._fallbacks(effective_by_identity, policy.fixed_model, task_profile),
            )

        if policy.selection_mode is SelectionMode.CANDIDATE_GROUP:
            if isinstance(policy, StageModelPolicy):
                if (
                    candidate_group is None
                    or candidate_group.group_id != policy.candidate_group_id
                    or candidate_group.revision != policy.candidate_group_revision
                    or candidate_group.stage is not task_profile.stage
                ):
                    raise ModelSelectionError("MODEL_SELECTION_FAILED")
                rejected: list[CandidateRejection] = []
                selected: BoundModelIdentity | None = None
                for candidate in sorted(candidate_group.candidates, key=lambda item: item.priority):
                    identity = candidate.identity
                    try:
                        if self._identity_key(identity) not in effective_by_identity:
                            raise ModelSelectionError("MODEL_NOT_ALLOWED")
                        self._gate(identity, task_profile)
                        if candidate_eligibility is not None:
                            candidate_eligibility(identity)
                    except ModelSelectionError as error:
                        rejected.append(CandidateRejection(candidate=identity, rejection_reason=error.code))
                        continue
                    selected = identity
                    break
                evidence = CandidateSelectionEvidence(
                    candidate_group_id=candidate_group.group_id,
                    candidate_group_revision=candidate_group.revision,
                    candidate_list=tuple(sorted(candidate_group.candidates, key=lambda item: item.priority)),
                    rejected_candidates=tuple(rejected),
                    selected_candidate=selected,
                    selection_policy=candidate_group.policy,
                )
                return ModelSelectionResult(
                    selected_model=selected,
                    selection_reason="CANDIDATE_GROUP_PRIORITY_ORDER" if selected is not None else "CANDIDATE_GROUP_FAILED",
                    candidate_selection_evidence=evidence,
                )
            candidates = tuple(policy.candidate_models)
            checked: list[BoundModelIdentity] = []
            for candidate in candidates:
                if self._identity_key(candidate) not in effective_by_identity:
                    raise ModelSelectionError("MODEL_NOT_ALLOWED")
                self._gate(candidate, task_profile)
                checked.append(candidate)
            return ModelSelectionResult(
                selected_model=None,
                selection_reason="CANDIDATE_GROUP_PENDING",
                fallback_candidates=tuple(checked),
            )

        return self._select_auto(
            task_profile,
            policy,
            self.effective_policy_overlays(allowed_models, project_id=task_profile.project_id, stage_id=task_profile.stage),
            candidate_eligibility,
        )

    def _select_auto(
        self,
        task: ModelSelectionTaskProfile,
        policy: StageModelPolicy | ProjectDefaultModelPolicy,
        candidates: tuple[AllowedModelPolicy, ...],
        candidate_eligibility: Callable[[BoundModelIdentity], None] | None,
    ) -> ModelSelectionResult:
        ordered = sorted(candidates, key=lambda item: (item.priority, self._policy_identity(item)))
        listed = tuple(CandidateSpec(identity=self._identity_from_policy(item), priority=item.priority) for item in ordered)
        accepted: list[CandidateSpec] = []
        rejected: list[CandidateRejection] = []
        insufficient = False
        for source, candidate in zip(ordered, listed, strict=True):
            try:
                if not source.enabled:
                    raise ModelSelectionError("MODEL_NOT_ALLOWED")
                self._gate(candidate.identity, task)
                if candidate_eligibility is None:
                    raise ModelSelectionError("INSUFFICIENT_EVIDENCE")
                candidate_eligibility(candidate.identity)
            except ModelSelectionError as error:
                rejected.append(CandidateRejection(candidate=candidate.identity, rejection_reason=error.code))
                insufficient |= error.insufficient or error.code == "INSUFFICIENT_EVIDENCE"
                continue
            accepted.append(candidate)
        selected = accepted[0].identity if accepted else None
        reason = (
            "AUTO_PRIORITY_THEN_IDENTITY_V1" if selected is not None
            else "AUTO_INSUFFICIENT_EVIDENCE" if insufficient else "AUTO_SELECTION_FAILED"
        )
        evidence = AutoSelectionEvidence(
            auto_policy_revision=canonical_fingerprint(policy, exclude=set()),
            candidate_source="ALLOWED_MODEL_POOL:" + hashlib.sha256(
                json.dumps(
                    [item.model_dump(mode="json") for item in ordered],
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest(),
            candidate_list=listed,
            filtered_candidates=tuple(accepted),
            rejection_reasons=tuple(rejected),
            selected_candidate=selected,
            selection_reason=reason,
        )
        return ModelSelectionResult(selected_model=selected, selection_reason=reason, auto_selection_evidence=evidence)

    @staticmethod
    def effective_allowed(
        policies: Sequence[AllowedModelPolicy],
        *,
        project_id: str,
        stage_id: EngineeringStage,
    ) -> tuple[AllowedModelPolicy, ...]:
        return tuple(
            item
            for item in ModelSelectionService.effective_policy_overlays(
                policies, project_id=project_id, stage_id=stage_id
            )
            if item.enabled
        )

    @staticmethod
    def effective_policy_overlays(
        policies: Sequence[AllowedModelPolicy],
        *,
        project_id: str,
        stage_id: EngineeringStage,
    ) -> tuple[AllowedModelPolicy, ...]:
        selected: dict[tuple[str, str, str, str, str], AllowedModelPolicy] = {}
        ranks = {PolicyScope.GLOBAL: 0, PolicyScope.PROJECT: 1, PolicyScope.STAGE: 2}
        for policy in policies:
            if policy.scope is PolicyScope.GLOBAL:
                applicable = True
            elif policy.scope is PolicyScope.PROJECT:
                applicable = policy.project_id == project_id
            else:
                applicable = policy.project_id == project_id and policy.stage_id is stage_id
            if not applicable:
                continue
            key = ModelSelectionService._policy_identity(policy)
            current = selected.get(key)
            if current is None or ranks[policy.scope] >= ranks[current.scope]:
                selected[key] = policy
        return tuple(
            sorted(
                selected.values(),
                key=lambda item: (item.priority, item.id),
            )
        )

    def _auto_result(
        self,
        effective: Mapping[tuple[str, str, str, str, str], AllowedModelPolicy],
        task_profile: ModelSelectionTaskProfile,
        reason: str,
    ) -> ModelSelectionResult:
        candidates: list[BoundModelIdentity] = []
        for policy in effective.values():
            identity = self._identity_from_policy(policy)
            if not self._strict_gates:
                self._gate(identity, task_profile)
            candidates.append(identity)
        return ModelSelectionResult(selected_model=None, selection_reason=reason, fallback_candidates=tuple(candidates))

    def _fallbacks(
        self,
        effective: Mapping[tuple[str, str, str, str, str], AllowedModelPolicy],
        selected: BoundModelIdentity,
        task_profile: ModelSelectionTaskProfile,
    ) -> tuple[BoundModelIdentity, ...]:
        result: list[BoundModelIdentity] = []
        for policy in effective.values():
            identity = self._identity_from_policy(policy)
            if self._identity_key(identity) == self._identity_key(selected):
                continue
            try:
                self._gate(identity, task_profile)
            except ModelSelectionError:
                continue
            result.append(identity)
        return tuple(result)

    def _gate(self, identity: BoundModelIdentity, task: ModelSelectionTaskProfile) -> None:
        model = self._models.get(identity.model_profile_id)
        if model is None:
            raise ModelSelectionError("MODEL_NOT_FOUND")
        if model.provider_id != identity.provider_id:
            raise ModelSelectionError("MODEL_PROVIDER_MISMATCH")
        if model.endpoint_id != identity.endpoint_id:
            raise ModelSelectionError("MODEL_ENDPOINT_MISMATCH")
        if model.remote_model_id != identity.model_id:
            raise ModelSelectionError("MODEL_MODEL_ID_MISMATCH")
        expected_credential = self._credential_ref_by_endpoint.get(identity.endpoint_id)
        if expected_credential is not None and expected_credential != identity.credential_ref:
            raise ModelSelectionError("MODEL_CREDENTIAL_MISMATCH")
        if expected_credential is not None and identity.credential_ref not in self._available_credentials:
            raise ModelSelectionError("MODEL_CREDENTIAL_GATE_FAILED")
        required = set(task.required_capabilities)
        stage = self._stages.get(task.stage)
        if stage is not None:
            required = required | set(stage.required_capabilities)
        if not required.issubset(set(model.capabilities)):
            raise ModelSelectionError("MODEL_CAPABILITY_MISMATCH")
        by_capability = {item.capability: item for item in model.capability_sources}
        for capability in required:
            evidence = by_capability.get(capability)
            if (
                evidence is None
                or evidence.status is not CapabilityStatus.PASS
                or not evidence.matches_model_identity(
                    provider_id=model.provider_id,
                    endpoint_id=model.endpoint_id,
                    credential_ref=identity.credential_ref,
                    model_profile_id=model.model_profile_id,
                    model_id=model.remote_model_id,
                    credential_revision=model.credential_revision,
                    endpoint_revision=model.endpoint_revision,
                )
            ):
                raise ModelSelectionError("MODEL_CAPABILITY_MISMATCH", insufficient=evidence is None or evidence.status in {CapabilityStatus.UNKNOWN, CapabilityStatus.UNAVAILABLE, CapabilityStatus.STALE})
        allowed_availability = (
            {ModelAvailability.AVAILABLE}
            if self._strict_gates
            else {ModelAvailability.AVAILABLE, ModelAvailability.RATE_LIMITED}
        )
        if model.availability_status not in allowed_availability:
            raise ModelSelectionError("MODEL_AVAILABILITY_GATE_FAILED")
        health = self._endpoint_health.get(identity.endpoint_id)
        if health in {None, "UNKNOWN"} and not self._strict_gates:
            health = "AVAILABLE"
        if health is None or health not in {"AVAILABLE", "QUOTA_EXHAUSTED"}:
            raise ModelSelectionError("MODEL_AVAILABILITY_GATE_FAILED", insufficient=health is None or health == "UNKNOWN")
        quota = self._quota_available.get(identity.endpoint_id)
        if quota is None and not self._strict_gates:
            quota = True
        if health == "QUOTA_EXHAUSTED" or quota is not True:
            raise ModelSelectionError("MODEL_QUOTA_GATE_FAILED", insufficient=quota is None)

    @staticmethod
    def _policy_identity(item: AllowedModelPolicy) -> tuple[str, str, str, str, str]:
        return (item.provider_id, item.endpoint_id, item.credential_ref, item.model_profile_id, item.model_id)

    @staticmethod
    def _identity_key(item: BoundModelIdentity) -> tuple[str, str, str, str, str]:
        return (item.provider_id, item.endpoint_id, item.credential_ref, item.model_profile_id, item.model_id)

    @staticmethod
    def _identity_key_from_identity(item: BoundModelIdentity) -> tuple[str, str, str, str, str]:
        return ModelSelectionService._identity_key(item)

    @staticmethod
    def _identity_from_policy(item: AllowedModelPolicy) -> BoundModelIdentity:
        return BoundModelIdentity(
            provider_id=item.provider_id,
            endpoint_id=item.endpoint_id,
            credential_ref=item.credential_ref,
            model_profile_id=item.model_profile_id,
            model_id=item.model_id,
        )


__all__ = ("ModelSelectionError", "ModelSelectionService")
