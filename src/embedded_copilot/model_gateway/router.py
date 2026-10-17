"""Deterministic capability-first model routing for the canonical gateway."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from .metrics import ModelReliability
from .profiles import (
    BillingClass,
    CapabilityStatus,
    ModelProfile,
    ModelSelection,
    PaidModelPolicy,
    QualityTier,
    RequestModelOverride,
    RoutingRequest,
    RoutingStrategy,
    TaskAssignment,
)


class ModelRoutingError(ValueError):
    """A safe, stable routing rejection; it never contains provider details."""

    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        super().__init__(message or code)


class PaidModelGuard:
    """Apply the billing policy after all capability and quality gates."""

    @staticmethod
    def check(model: ModelProfile, policy: PaidModelPolicy) -> None:
        if model.billing_class in {BillingClass.FREE, BillingClass.LOCAL}:
            return
        if policy is PaidModelPolicy.ASK:
            raise ModelRoutingError("PAID_MODEL_APPROVAL_REQUIRED")
        if policy is PaidModelPolicy.DENY:
            raise ModelRoutingError("PAID_MODEL_DENIED")


class ModelRouter:
    """Select one eligible model with stable, explainable ordering."""

    def __init__(self, *, minimum_sample_size: int | None = None) -> None:
        if minimum_sample_size is not None and (type(minimum_sample_size) is not int or minimum_sample_size < 1):
            raise ValueError("minimum sample size is invalid")
        self._minimum_sample_size = minimum_sample_size

    def select(
        self,
        request: RoutingRequest,
        models: Sequence[ModelProfile],
        *,
        reliability: Mapping[str, ModelReliability] | None = None,
    ) -> ModelSelection:
        if type(request) is not RoutingRequest:
            raise TypeError("routing request is invalid")
        if not models:
            raise ModelRoutingError("NO_ELIGIBLE_MODEL")
        checked_models = tuple(models)
        for model in checked_models:
            if type(model) is not ModelProfile:
                raise TypeError("model profile is invalid")

        policy = request.global_policy
        minimum_sample_size = self._minimum_sample_size or policy.minimum_sample_size
        endpoint_health = dict(request.endpoint_health)
        credential_ref_by_endpoint = dict(request.credential_ref_by_endpoint)
        disabled_models = set(policy.disabled_models)
        disabled_endpoints = set(policy.disabled_endpoints)
        if request.project_policy is not None:
            disabled_models = disabled_models | set(request.project_policy.disabled_models)
            disabled_endpoints = disabled_endpoints | set(request.project_policy.disabled_endpoints)

        eligible: list[ModelProfile] = []
        rejected_codes: list[str] = []
        for model in checked_models:
            code = self._gate(
                model,
                request,
                endpoint_health=endpoint_health,
                available_credentials=set(request.available_credential_refs),
                credential_ref_by_endpoint=credential_ref_by_endpoint,
                disabled_models=disabled_models,
                disabled_endpoints=disabled_endpoints,
            )
            if code is None:
                eligible.append(model)
            else:
                rejected_codes.append(code)

        target = self._requested_model(request)
        if target is not None:
            selected = next((item for item in checked_models if item.model_profile_id == target), None)
            if selected is None:
                raise ModelRoutingError("MODEL_NOT_FOUND")
            target_code = self._gate(
                selected,
                request,
                endpoint_health=endpoint_health,
                available_credentials=set(request.available_credential_refs),
                credential_ref_by_endpoint=credential_ref_by_endpoint,
                disabled_models=disabled_models,
                disabled_endpoints=disabled_endpoints,
            )
            if target_code is not None:
                raise ModelRoutingError(target_code)
            PaidModelGuard.check(selected, policy.paid_model_policy)
            strategy = self._strategy(request)
            reason = "request override" if self._override_id(request) else "task assignment"
            return ModelSelection(
                model_profile_id=selected.model_profile_id,
                strategy=strategy,
                reason=reason,
                fallback_allowed=request.task_profile.fallback_allowed and policy.fallback_allowed,
            )

        if not eligible:
            if "CAPABILITY_UNAVAILABLE" in rejected_codes:
                raise ModelRoutingError("CAPABILITY_UNAVAILABLE")
            if "QUALITY_BELOW_MINIMUM" in rejected_codes:
                raise ModelRoutingError("QUALITY_BELOW_MINIMUM")
            if "CREDENTIAL_UNAVAILABLE" in rejected_codes:
                raise ModelRoutingError("CREDENTIAL_UNAVAILABLE")
            raise ModelRoutingError("NO_ELIGIBLE_MODEL")

        strategy = self._strategy(request)
        ordered = sorted(
            eligible,
            key=lambda item: self._sort_key(
                item,
                strategy,
                request,
                reliability=reliability or {},
                minimum_sample_size=minimum_sample_size,
            ),
        )
        selected = ordered[0]
        PaidModelGuard.check(selected, policy.paid_model_policy)
        return ModelSelection(
            model_profile_id=selected.model_profile_id,
            strategy=strategy,
            reason=f"{strategy.value} deterministic ranking",
            fallback_allowed=request.task_profile.fallback_allowed and policy.fallback_allowed,
        )

    @staticmethod
    def _override_id(request: RoutingRequest) -> str | None:
        override = request.request_model_override
        if isinstance(override, RequestModelOverride):
            return override.model_profile_id if override.enabled else None
        return override

    def _requested_model(self, request: RoutingRequest) -> str | None:
        override = self._override_id(request)
        if override:
            return override
        assignment = self._assignment(request)
        if assignment is not None and assignment.enabled:
            return assignment.model_profile_id
        return request.global_policy.active_model_profile_id

    @staticmethod
    def _assignment(request: RoutingRequest) -> TaskAssignment | None:
        policy = request.project_policy
        if policy is None:
            return None
        return next((item for item in policy.task_assignments if item.task_type is request.task_profile.task_type), None)

    @staticmethod
    def _strategy(request: RoutingRequest) -> RoutingStrategy:
        if request.routing_strategy is not None:
            return request.routing_strategy
        if request.project_policy is not None and request.project_policy.default_strategy is not None:
            return request.project_policy.default_strategy
        if request.global_policy.default_strategy is not None:
            return request.global_policy.default_strategy
        return request.task_profile.default_routing_strategy

    @staticmethod
    def _gate(
        model: ModelProfile,
        request: RoutingRequest,
        *,
        endpoint_health: Mapping[str, str],
        available_credentials: set[str],
        credential_ref_by_endpoint: Mapping[str, str],
        disabled_models: set[str],
        disabled_endpoints: set[str],
    ) -> str | None:
        if not model.enabled or model.model_profile_id in disabled_models:
            return "MODEL_DISABLED"
        if model.endpoint_id in disabled_endpoints:
            return "ENDPOINT_DISABLED"
        health = endpoint_health.get(model.endpoint_id, model.metadata_value("health_status") if hasattr(model, "metadata_value") else "UNKNOWN")
        if health in {"UNAVAILABLE", "AUTH_FAILED", "QUOTA_EXHAUSTED"}:
            return "ENDPOINT_UNAVAILABLE"
        required = set(request.task_profile.required_capabilities)
        available = set(model.capabilities)
        if not required.issubset(available):
            return "CAPABILITY_UNAVAILABLE"
        for evidence in model.capability_sources:
            if evidence.capability in required and evidence.status in {CapabilityStatus.FAIL, CapabilityStatus.UNAVAILABLE, CapabilityStatus.STALE, CapabilityStatus.UNKNOWN}:
                return "CAPABILITY_UNAVAILABLE"
        quality_rank = {QualityTier.UNKNOWN: 0, QualityTier.STANDARD: 1, QualityTier.HIGH: 2, QualityTier.PREMIUM: 3}
        if quality_rank[model.quality_tier] < quality_rank[request.task_profile.minimum_quality_tier]:
            return "QUALITY_BELOW_MINIMUM"
        if model.billing_class is not BillingClass.LOCAL:
            expected_credential = credential_ref_by_endpoint.get(model.endpoint_id)
            if expected_credential is not None:
                if expected_credential not in available_credentials:
                    return "CREDENTIAL_UNAVAILABLE"
            elif not available_credentials:
                return "CREDENTIAL_UNAVAILABLE"
        return None

    @staticmethod
    def _quality_rank(model: ModelProfile) -> int:
        return {QualityTier.UNKNOWN: 0, QualityTier.STANDARD: 1, QualityTier.HIGH: 2, QualityTier.PREMIUM: 3}[model.quality_tier]

    @staticmethod
    def _billing_rank(model: ModelProfile) -> int:
        return {
            BillingClass.FREE: 0,
            BillingClass.LOCAL: 1,
            BillingClass.PAID: 2,
            BillingClass.METERED: 2,
            BillingClass.SUBSCRIPTION: 2,
            BillingClass.MIXED: 3,
            BillingClass.UNKNOWN: 4,
        }[model.billing_class]

    @staticmethod
    def _known_cost(model: ModelProfile) -> float:
        if model.pricing is None or model.pricing.input_token_price is None or model.pricing.output_token_price is None:
            return float("inf")
        return model.pricing.input_token_price + model.pricing.output_token_price

    @staticmethod
    def _preference(model: ModelProfile, request: RoutingRequest) -> int:
        if request.preferred_model == model.model_profile_id:
            return 0
        preferred = request.preferred_provider or next(iter(request.global_policy.preferred_providers), None)
        return 0 if preferred and model.provider_id == preferred else 1

    @classmethod
    def _sort_key(
        cls,
        model: ModelProfile,
        strategy: RoutingStrategy,
        request: RoutingRequest,
        *,
        reliability: Mapping[str, ModelReliability],
        minimum_sample_size: int,
    ) -> tuple[object, ...]:
        observed = reliability.get(model.model_profile_id)
        reliable = observed is not None and observed.request_attempts >= minimum_sample_size
        score = observed.request_success_rate if reliable and observed.request_success_rate is not None else model.reliability_score
        reliability_sort = -(score if score is not None else -1.0)
        preference = cls._preference(model, request)
        quality = -cls._quality_rank(model)
        billing = cls._billing_rank(model)
        cost = cls._known_cost(model)
        if strategy is RoutingStrategy.QUALITY_FIRST:
            return (preference, quality, reliability_sort, billing, cost, model.model_profile_id)
        if strategy is RoutingStrategy.LOWEST_COST:
            return (preference, billing, cost, quality, reliability_sort, model.model_profile_id)
        return (preference, quality, reliability_sort, billing, cost, model.model_profile_id)


__all__ = ("ModelRouter", "ModelRoutingError", "PaidModelGuard")
