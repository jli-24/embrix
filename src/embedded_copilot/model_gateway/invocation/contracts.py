"""Secret-free contracts for governed model invocation."""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import (
    Field,
    ValidationInfo,
    field_validator,
    model_serializer,
    model_validator,
)

from embedded_copilot.correlation import CorrelationContext

from ..contracts import (
    EngineeringContextSnapshot,
    ModelGatewayContract,
    ModelImageInput,
    ModelMessage,
    ModelRequest,
    ModelRequestMetadata,
    ModelResponse,
    ModelUsage,
    canonical_fingerprint,
    identifier,
    safe_text,
)
from ..contracts import (
    fingerprint as validate_fingerprint,
)
from ..governance import (
    AutoSelectionEvidence,
    BoundModelIdentity,
    CandidateSelectionEvidence,
    EngineeringStage,
    ModelSelectionTaskProfile,
    SelectionMode,
)
from ..profiles import ModelCapability, ProtocolFamily
from .bundle_contracts import (
    ConditionalGenerationCeilingV1,
    CounterAdmissionV1,
    CounterObservationV1,
    SingleInvocationBudgetEnvelopeV2,
)


class InvocationStatus(StrEnum):
    SUCCESS = "SUCCESS"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


ModelInvocationStatus = InvocationStatus


class GateStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"


class AttemptStatus(StrEnum):
    PREPARED = "PREPARED"
    DISPATCH_INTENT = "DISPATCH_INTENT"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"
    COMPLETED = "COMPLETED"


class InvocationMode(StrEnum):
    CHAT = "CHAT"
    TASK = "TASK"


class TaskAuthorityFailure(ValueError):
    """Fixed safe reason from a caller-bound Task authority source."""

    def __init__(self, code: str = "TASK_AUTHORITY_STALE") -> None:
        if code not in {"TASK_AUTHORITY_STALE", "TASK_AUTHORITY_OWNER_UNAVAILABLE", "TASK_MODEL_OPERATION_UNAVAILABLE", "TASK_STAGE_BINDING_MISMATCH"}:
            raise ValueError("task authority failure code is invalid")
        super().__init__(code)
        self.code = code


class TaskAuthorityReference(ModelGatewayContract):
    tenant_id: str
    project_id: str
    engineering_task_id: str
    task_request_fingerprint: str
    coordination_fingerprint: str
    plan_id: str
    plan_content_fingerprint: str
    review_fingerprint: str
    phase_id: str
    phase_fingerprint: str
    plan_task_id: str
    operation_id: str
    operation_binding_fingerprint: str

    @model_validator(mode="after")
    def _validate(self) -> TaskAuthorityReference:
        for name in ("tenant_id", "project_id", "engineering_task_id", "plan_id", "phase_id", "plan_task_id", "operation_id"):
            identifier(getattr(self, name), field=name)
        for name in ("task_request_fingerprint", "coordination_fingerprint", "plan_content_fingerprint", "review_fingerprint", "phase_fingerprint", "operation_binding_fingerprint"):
            validate_fingerprint(getattr(self, name), field=name)
        if self.operation_id not in {"HARDWARE_SELECTION_PROPOSAL", "SCHEMATIC_DESIGN_PROPOSAL"}:
            raise ValueError("task operation is unavailable")
        return self


class ModelExecutionSnapshot(ModelGatewayContract):
    schema_version: int = Field(default=1, ge=1, le=2)
    connection_revision: str | None = None
    project_id: str
    stage_id: EngineeringStage
    selected_model: BoundModelIdentity
    connection_id: str
    credential_revision: str
    endpoint_revision: str
    policy_source: str
    policy_fingerprint: str
    selection_fingerprint: str
    fingerprint: str = "sha256:" + "0" * 64

    @model_validator(mode="after")
    def _verify(self) -> ModelExecutionSnapshot:
        for name in ("project_id", "connection_id", "credential_revision", "endpoint_revision", "policy_source"):
            identifier(getattr(self, name), field=name)
        for name in ("policy_fingerprint", "selection_fingerprint"):
            validate_fingerprint(getattr(self, name), field=name)
        if self.schema_version == 2:
            validate_fingerprint(self.connection_revision, field="connection_revision")
        elif self.connection_revision is not None:
            raise ValueError("legacy snapshot cannot contain connection revision")
        expected = canonical_fingerprint(self, exclude={"fingerprint"})
        if self.fingerprint == "sha256:" + "0" * 64:
            object.__setattr__(self, "fingerprint", expected)
        elif self.fingerprint != expected:
            raise ValueError("execution snapshot fingerprint mismatch")
        return self


    @model_serializer(mode="wrap")
    def _legacy_json(self, handler):
        value = handler(self)
        if self.schema_version == 1:
            value.pop("schema_version", None)
            value.pop("connection_revision", None)
        return value


class SingleInvocationBudgetEnvelopeV1(ModelGatewayContract):
    schema_version: int = Field(default=1, ge=1, le=1)
    envelope_id: str
    tenant_id: str
    project_id: str
    conversation_id: str
    task_id: str
    task_authority_fingerprint: str
    engineering_stage: EngineeringStage
    purpose: str = "REAL_STAGE_ACCEPTANCE"
    currency: str = "USD"
    max_cost_decimal: str = "0.10"
    max_requests: int = Field(default=1, ge=1, le=1)
    not_after: datetime
    fingerprint: str = "sha256:" + "0" * 64

    @model_validator(mode="after")
    def _verify(self):
        for name in ("envelope_id", "tenant_id", "project_id", "conversation_id", "task_id"):
            identifier(getattr(self, name), field=name)
        validate_fingerprint(self.task_authority_fingerprint)
        if self.purpose != "REAL_STAGE_ACCEPTANCE" or self.currency != "USD" or self.max_cost_decimal != "0.10" or self.not_after.utcoffset() is None:
            raise ValueError("single invocation budget envelope is invalid")
        expected = canonical_fingerprint(self, exclude={"fingerprint"})
        if self.fingerprint == "sha256:" + "0" * 64:
            object.__setattr__(self, "fingerprint", expected)
        elif self.fingerprint != expected:
            raise ValueError("budget envelope fingerprint mismatch")
        return self


class InvocationBudgetEvidenceV1(ModelGatewayContract):
    schema_version: int = Field(default=1, ge=1, le=1)
    request_fingerprint: str
    execution_snapshot_fingerprint: str
    final_transport_sha256: str
    selected_model: BoundModelIdentity
    connection_id: str
    connection_revision: str
    credential_revision: str
    endpoint_revision: str
    protocol_family: ProtocolFamily
    pricing_conditions: tuple[tuple[str, str], ...] = ()
    count_kind: str
    input_token_bound: int = Field(ge=0)
    counter_identity: str
    counter_revision: str
    qualification_evidence_fingerprint: str
    count_not_after: datetime
    output_token_limit: int = Field(ge=1)
    provider_parameter: str
    enforcement_evidence_fingerprint: str
    covers_all_billable_input: bool
    covers_all_billable_output: bool
    includes_reasoning: bool
    output_not_after: datetime
    pricing_identity: str
    pricing_revision: str
    source_sha256: str
    currency: str
    unit: str
    input_price_decimal: str
    output_price_decimal: str
    cache_read_price_decimal: str | None = None
    cache_write_price_decimal: str | None = None
    cache_semantics: str = "NONE"
    additional_fees_state: str
    additional_cost_bound_decimal: str
    pricing_not_after: datetime
    transport_not_after: datetime
    transport_evidence_fingerprint: str
    envelope_fingerprint: str
    input_cost_upper_fraction: str
    output_cost_upper_fraction: str
    additional_cost_upper_fraction: str
    total_cost_upper_fraction: str
    fingerprint: str = "sha256:" + "0" * 64

    @model_validator(mode="after")
    def _verify(self):
        for name in ("request_fingerprint", "execution_snapshot_fingerprint", "final_transport_sha256", "connection_revision", "qualification_evidence_fingerprint", "enforcement_evidence_fingerprint", "source_sha256", "transport_evidence_fingerprint", "envelope_fingerprint"):
            validate_fingerprint(getattr(self, name), field=name)
        for name in ("connection_id", "credential_revision", "endpoint_revision", "counter_identity", "counter_revision", "provider_parameter", "pricing_identity", "pricing_revision"):
            identifier(getattr(self, name), field=name)
        for name in ("count_not_after", "output_not_after", "pricing_not_after", "transport_not_after"):
            if getattr(self, name).utcoffset() is None:
                raise ValueError("budget evidence time invalid")
        expected = canonical_fingerprint(self, exclude={"fingerprint"})
        if self.fingerprint == "sha256:" + "0" * 64:
            object.__setattr__(self, "fingerprint", expected)
        elif self.fingerprint != expected:
            raise ValueError("budget evidence fingerprint mismatch")
        return self


_BUNDLE_ROLE_FIELDS = ('budget_phase', 'counter_admission_fingerprint', 'counter_observation_fingerprint', 'counter_status', 'counter_transport_send_count', 'counter_response_observed', 'generation_transport_send_count', 'total_external_request_count', 'counter_http_status', 'counter_input_tokens')


def _validate_bundle_roles(value):
    if value.budget_phase is None or value.counter_status is None:
        raise ValueError("bundle role phase is missing")
    if value.budget_phase == "PREPARED" or (value.budget_phase == "TERMINAL" and value.budget_envelope_id is None):
        if any(getattr(value, name) is not None for name in ("budget_envelope_id", "budget_reservation_id", "counter_admission_fingerprint", "counter_observation_fingerprint", "budget_evidence_fingerprint")):
            raise ValueError("prepared bundle cannot claim reserved evidence")
        if value.counter_status != "NOT_ATTEMPTED" or value.counter_response_observed or (value.counter_transport_send_count, value.generation_transport_send_count, value.total_external_request_count) != (0, 0, 0):
            raise ValueError("prepared bundle cannot claim an external operation")
    else:
        for name in ("budget_envelope_id", "budget_reservation_id"):
            identifier(getattr(value, name), field=name)
        validate_fingerprint(value.counter_admission_fingerprint)
    for name in ("counter_admission_fingerprint", "counter_observation_fingerprint", "budget_evidence_fingerprint"):
        item = getattr(value, name)
        if item is not None:
            validate_fingerprint(item)
    if value.counter_response_observed:
        if value.counter_transport_send_count != 1 or value.counter_http_status is None or value.counter_observation_fingerprint is None:
            raise ValueError("counter response requires its immutable observation")
    elif value.counter_http_status is not None or value.counter_input_tokens is not None:
        raise ValueError("unobserved counter cannot claim response facts")
    if value.counter_status == "COUNTED" and (not value.counter_response_observed or value.counter_http_status != 200 or value.counter_input_tokens is None):
        raise ValueError("counted counter requires a successful integer observation")
    if value.budget_phase == "COUNTER_INTENT" and (
        value.counter_status != "DISPATCH_INTENT" or value.counter_response_observed
        or value.counter_transport_send_count is not None or value.generation_transport_send_count != 0
    ):
        raise ValueError("counter intent role is invalid")
    if value.budget_phase == "COUNTER_OBSERVED" and not value.counter_response_observed:
        raise ValueError("counter observed phase requires response")
    if value.budget_phase == "GENERATION_INTENT" and (
        value.counter_status != "COUNTED" or value.counter_input_tokens > 8192
        or value.budget_evidence_fingerprint is None
    ):
        raise ValueError("generation intent requires admitted measured count")
    if value.generation_transport_send_count == 1 and (
        value.counter_status != "COUNTED" or value.counter_input_tokens > 8192
        or value.budget_evidence_fingerprint is None
    ):
        raise ValueError("generation cannot precede admitted counter")
    counts = (value.counter_transport_send_count, value.generation_transport_send_count)
    total = None if any(item is None for item in counts) else sum(counts)
    if value.total_external_request_count != total:
        raise ValueError("external operation total cannot hide UNKNOWN")
    return value


class ModelInvocationAttempt(ModelGatewayContract):
    budget_phase: Literal["PREPARED", "COUNTER_INTENT", "COUNTER_OBSERVED", "GENERATION_INTENT", "TERMINAL"] | None = None
    counter_admission_fingerprint: str | None = None
    counter_observation_fingerprint: str | None = None
    counter_status: Literal["NOT_ATTEMPTED", "DISPATCH_INTENT", "COUNTED", "HTTP_ERROR", "INVALID_RESPONSE", "OUTCOME_UNKNOWN"] | None = None
    counter_transport_send_count: int | None = Field(default=None, strict=True, ge=0, le=1)
    counter_response_observed: bool = False
    generation_transport_send_count: int | None = Field(default=None, strict=True, ge=0, le=1)
    total_external_request_count: int | None = Field(default=None, strict=True, ge=0, le=2)
    counter_http_status: int | None = Field(default=None, strict=True, ge=100, le=599)
    counter_input_tokens: int | None = Field(default=None, strict=True, ge=0)
    attempt_schema_version: int = Field(default=1, ge=1, le=4)
    budget_envelope_id: str | None = None
    budget_evidence_fingerprint: str | None = None
    budget_reservation_id: str | None = None
    project_id: str
    request_id: str
    request_fingerprint: str
    snapshot: ModelExecutionSnapshot
    status: AttemptStatus = AttemptStatus.PREPARED
    confirmed_provider_request_count: int | None = Field(default=None, ge=0, le=1)
    invocation_mode: InvocationMode | None = None
    authority_reference: TaskAuthorityReference | None = None
    gateway_call_count: int | None = Field(default=None, ge=0, le=1)
    gateway_response_observed: bool = False
    transport_send_count: int | None = Field(default=None, ge=0)
    receipt_fingerprint: str | None = None
    fingerprint: str = "sha256:" + "0" * 64

    @model_validator(mode="after")
    def _verify(self) -> ModelInvocationAttempt:
        if self.attempt_schema_version >= 2:
            if self.invocation_mode is None or (self.invocation_mode is InvocationMode.TASK) != (self.authority_reference is not None):
                raise ValueError("attempt authority binding is invalid")
            if self.authority_reference is not None and self.authority_reference.project_id != self.project_id:
                raise ValueError("attempt authority project mismatch")
        elif self.invocation_mode is not None or self.authority_reference is not None or self.gateway_call_count is not None or self.gateway_response_observed or self.transport_send_count is not None:
            raise ValueError("legacy attempt cannot contain v2 fields")
        identifier(self.project_id, field="project_id")
        identifier(self.request_id, field="request_id")
        if self.snapshot.project_id != self.project_id:
            raise ValueError("attempt identity mismatch")
        validate_fingerprint(self.request_fingerprint, field="request_fingerprint")
        if self.status is AttemptStatus.PREPARED and (self.confirmed_provider_request_count is not None or self.receipt_fingerprint is not None or self.gateway_call_count is not None or self.gateway_response_observed):
            raise ValueError("prepared attempt is invalid")
        if self.attempt_schema_version < 4 and self.status in {AttemptStatus.DISPATCH_INTENT, AttemptStatus.OUTCOME_UNKNOWN} and (self.confirmed_provider_request_count is not None or self.receipt_fingerprint is not None or self.gateway_response_observed):
            raise ValueError("uncertain attempt cannot claim a provider call")
        if self.status is AttemptStatus.COMPLETED and self.attempt_schema_version == 1 and (self.receipt_fingerprint is None or self.confirmed_provider_request_count != 1):
            raise ValueError("completed attempt receipt is missing")
        if self.status is AttemptStatus.COMPLETED and self.attempt_schema_version in {2, 3} and (self.receipt_fingerprint is None or self.confirmed_provider_request_count is not None or self.gateway_call_count != 1 or not self.gateway_response_observed):
            raise ValueError("completed v2 attempt receipt is missing")
        if self.receipt_fingerprint is not None:
            validate_fingerprint(self.receipt_fingerprint, field="receipt_fingerprint")
        budget_fields = (self.budget_envelope_id, self.budget_evidence_fingerprint, self.budget_reservation_id)
        if self.attempt_schema_version < 3 and any(v is not None for v in budget_fields):
            raise ValueError("legacy attempt cannot contain budget fields")
        if self.attempt_schema_version == 3:
            if self.invocation_mode is not InvocationMode.TASK or self.snapshot.schema_version != 2:
                raise ValueError("budget attempt requires TASK snapshot v2")
            if any(v is not None for v in budget_fields) and not all(v is not None for v in budget_fields):
                raise ValueError("partial budget attempt binding")
            if self.status is not AttemptStatus.PREPARED and not all(v is not None for v in budget_fields):
                raise ValueError("dispatch intent budget binding missing")
            if self.budget_envelope_id is not None:
                identifier(self.budget_envelope_id, field="budget_envelope_id")
                identifier(self.budget_reservation_id, field="budget_reservation_id")
                validate_fingerprint(self.budget_evidence_fingerprint)
        if self.attempt_schema_version < 4:
            if any(getattr(self, name) is not None for name in _BUNDLE_ROLE_FIELDS if name != "counter_response_observed") or self.counter_response_observed:
                raise ValueError("legacy attempt cannot contain bundle role fields")
        else:
            if self.invocation_mode is not InvocationMode.TASK or self.snapshot.schema_version != 2:
                raise ValueError("bundle attempt requires TASK snapshot v2")
            if self.confirmed_provider_request_count is not None:
                raise ValueError("bundle attempt cannot reinterpret the legacy provider count")
            _validate_bundle_roles(self)
            if self.status is AttemptStatus.PREPARED and self.budget_phase != "PREPARED":
                raise ValueError("prepared bundle attempt phase mismatch")
            if self.status is AttemptStatus.DISPATCH_INTENT and self.budget_phase not in {"COUNTER_INTENT", "COUNTER_OBSERVED", "GENERATION_INTENT"}:
                raise ValueError("active bundle attempt phase mismatch")
            if self.status in {AttemptStatus.COMPLETED, AttemptStatus.OUTCOME_UNKNOWN} and self.budget_phase != "TERMINAL":
                raise ValueError("terminal bundle attempt phase mismatch")
            if self.status is AttemptStatus.COMPLETED and self.receipt_fingerprint is None:
                raise ValueError("completed bundle requires a terminal receipt")
        excluded = {"fingerprint"}
        if self.attempt_schema_version == 1:
            excluded.update({"attempt_schema_version", "invocation_mode", "authority_reference", "gateway_call_count", "gateway_response_observed", "transport_send_count"})
        expected = canonical_fingerprint(self, exclude=excluded)
        if self.fingerprint == "sha256:" + "0" * 64:
            object.__setattr__(self, "fingerprint", expected)
        elif self.fingerprint != expected:
            raise ValueError("invocation attempt fingerprint mismatch")
        return self


    @model_serializer(mode="wrap")
    def _legacy_json(self, handler):
        value = handler(self)
        if self.attempt_schema_version < 4:
            for name in _BUNDLE_ROLE_FIELDS:
                value.pop(name, None)
        if self.attempt_schema_version < 3:
            for name in ("budget_envelope_id", "budget_evidence_fingerprint", "budget_reservation_id"):
                value.pop(name, None)
        return value


class ModelInvocationTraceContext(ModelGatewayContract):
    trace_id: str
    parent_span_id: str | None = None
    correlation: CorrelationContext | None = None

    @field_validator("trace_id", "parent_span_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str | None:
        return None if value is None else identifier(value, field=info.field_name)

    @field_validator("correlation", mode="before")
    @classmethod
    def _correlation(cls, value: object) -> CorrelationContext | None:
        if value is None:
            return None
        return CorrelationContext.model_validate(
            copy.deepcopy(value.model_dump(mode="python") if isinstance(value, CorrelationContext) else value)
        )

    @model_validator(mode="after")
    def _trace_binding(self) -> "ModelInvocationTraceContext":
        if self.correlation is not None and self.correlation.trace_id != self.trace_id:
            raise ValueError("invocation trace correlation mismatch")
        return self


class ModelInvocationGenerationOptions(ModelGatewayContract):
    service_tier: Literal["default"] | None = None
    temperature: float = 0.0
    max_tokens: int = 1024
    tool_definitions: tuple[tuple[str, str], ...] = ()
    structured_output_schema: tuple[tuple[str, str], ...] | None = None


    @field_validator("temperature", "max_tokens", mode="before")
    @classmethod
    def _numbers(cls, value: object, info: ValidationInfo) -> float | int:
        if value is None:
            return 0.0 if info.field_name == "temperature" else 1024
        if info.field_name == "temperature":
            return ModelRequest.validate_provider_temperature(value)
        return ModelRequest.validate_provider_max_tokens(value)

    @field_validator("tool_definitions", "structured_output_schema", mode="before")
    @classmethod
    def _pairs(cls, value: object, info: ValidationInfo) -> tuple[tuple[str, str], ...] | None:
        return ModelRequest.validate_contract_pairs(value, info)

    @model_serializer(mode="wrap")
    def _legacy_service_tier(self, handler):
        value = handler(self)
        if self.service_tier is None:
            value.pop("service_tier", None)
        return value


class ModelInvocationPayload(ModelGatewayContract):
    task: str
    engineering_context: EngineeringContextSnapshot
    system_instruction: str
    constraints: tuple[str, ...] = ()
    messages: tuple[ModelMessage, ...] = ()
    attachments: tuple[str, ...] = ()
    images: tuple[ModelImageInput, ...] = ()
    conversation_id: str | None = None
    metadata: ModelRequestMetadata | None = None
    fingerprint: str = "sha256:" + "0" * 64

    @field_validator("task", "engineering_context", "system_instruction", "constraints", "messages", "attachments", "images", "metadata", mode="before")
    @classmethod
    def _content(cls, value: object, info: ValidationInfo) -> object:
        readers = {
            "task": ModelRequest.validate_task,
            "engineering_context": ModelRequest.validate_context,
            "system_instruction": ModelRequest.validate_system_instruction,
            "constraints": ModelRequest.validate_constraints,
            "messages": ModelRequest.validate_messages,
            "attachments": ModelRequest.validate_attachments,
            "images": ModelRequest.validate_images,
            "metadata": ModelRequest.validate_metadata,
        }
        return readers[info.field_name](copy.deepcopy(value))

    @field_validator("conversation_id", mode="before")
    @classmethod
    def _conversation(cls, value: object) -> str | None:
        return None if value is None else identifier(value, field="conversation_id")

    @model_validator(mode="after")
    def _integrity(self) -> ModelInvocationPayload:
        expected = canonical_fingerprint(self, exclude={"fingerprint"})
        if self.fingerprint == "sha256:" + "0" * 64:
            object.__setattr__(self, "fingerprint", expected)
        elif self.fingerprint != expected:
            raise ValueError("invocation payload fingerprint mismatch")
        return self


class ModelInvocationGate(ModelGatewayContract):
    name: str
    status: GateStatus
    detail: str | None = None

    @field_validator("name", mode="before")
    @classmethod
    def _name(cls, value: object) -> str:
        return identifier(value, field="name")

    @field_validator("detail", mode="before")
    @classmethod
    def _detail(cls, value: object) -> str | None:
        return None if value is None else safe_text(value, field="detail", maximum=256)


_GATE_NAMES = ("capability", "availability", "credential", "endpoint_health", "quota")


class ModelInvocationRequest(ModelGatewayContract):
    request_id: str
    project_id: str
    stage_id: EngineeringStage
    invocation_mode: InvocationMode
    authority_reference: TaskAuthorityReference | None = None
    task_type: str
    payload: ModelInvocationPayload
    required_capabilities: tuple[ModelCapability, ...] = ()
    request_override: BoundModelIdentity | None = None
    generation_options: ModelInvocationGenerationOptions = Field(default_factory=ModelInvocationGenerationOptions)
    trace_context: ModelInvocationTraceContext

    @field_validator("request_id", "project_id", "task_type", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("payload", mode="before")
    @classmethod
    def _request(cls, value: object) -> ModelInvocationPayload:
        return ModelInvocationPayload.model_validate(copy.deepcopy(value))

    @field_validator("required_capabilities", mode="before")
    @classmethod
    def _caps(cls, value: object) -> tuple[ModelCapability, ...]:
        if isinstance(value, list):
            value = tuple(value)
        if type(value) is not tuple:
            raise TypeError("required_capabilities must be a tuple")
        return tuple(item if isinstance(item, ModelCapability) else ModelCapability(str(item).upper()) for item in value)

    @model_validator(mode="after")
    def _binding(self) -> ModelInvocationRequest:
        if (self.invocation_mode is InvocationMode.TASK) != (self.authority_reference is not None):
            raise ValueError("invocation authority binding is invalid")
        if self.authority_reference is not None and self.authority_reference.project_id != self.project_id:
            raise ValueError("invocation authority project mismatch")
        if self.payload.engineering_context.project_id != self.project_id:
            raise ValueError("invocation project binding mismatch")
        if self.payload.metadata is not None and self.payload.metadata.request_id != self.request_id:
            raise ValueError("invocation request id mismatch")
        correlation = self.trace_context.correlation
        if correlation is not None and (
            correlation.request_id != self.request_id
            or correlation.project_id != self.project_id
        ):
            raise ValueError("invocation correlation binding mismatch")
        if self.payload.images and ModelCapability.VISION not in self.required_capabilities:
            object.__setattr__(self, "required_capabilities", self.required_capabilities + (ModelCapability.VISION,))
        return self

    @classmethod
    def from_model_request(
        cls,
        request: ModelRequest,
        *,
        stage_id: EngineeringStage,
        task_type: str | None = None,
        required_capabilities: tuple[ModelCapability, ...] = (),
        request_override: BoundModelIdentity | None = None,
        trace_context: ModelInvocationTraceContext | None = None,
    ) -> ModelInvocationRequest:
        project_id = request.project_id or request.engineering_context.project_id
        checked = ModelRequest.model_validate(copy.deepcopy(request.model_dump(mode="python")))
        options = ModelInvocationGenerationOptions(
            temperature=checked.temperature,
            service_tier=checked.model_policy.service_tier,
            max_tokens=checked.max_tokens,
            tool_definitions=checked.tool_definitions,
            structured_output_schema=checked.structured_output_schema,
        )
        return cls(
            request_id=checked.request_id,
            project_id=project_id,
            stage_id=stage_id,
            invocation_mode=InvocationMode.CHAT,
            task_type=task_type or checked.task_type or stage_id.value,
            payload=ModelInvocationPayload(**{key: value for key, value in checked.model_dump(mode="python").items() if key in ModelInvocationPayload.model_fields and key != "fingerprint"}),
            required_capabilities=tuple(dict.fromkeys((*required_capabilities, *checked.required_capabilities))),
            request_override=request_override,
            generation_options=options,
            trace_context=trace_context or ModelInvocationTraceContext(
                trace_id=checked.metadata.trace_id if checked.metadata else checked.request_id,
                correlation=CorrelationContext(
                    request_id=checked.request_id,
                    trace_id=checked.metadata.trace_id if checked.metadata else checked.request_id,
                    project_id=project_id,
                ),
            ),
        )


class ModelInvocationContext(ModelGatewayContract):
    task_profile: ModelSelectionTaskProfile
    policy_source: str
    selection_mode: SelectionMode
    selection_reason: str
    selected_model: BoundModelIdentity | None = None
    credential_revision: str | None = None
    gates: tuple[ModelInvocationGate, ...] = ()
    trace_context: ModelInvocationTraceContext

    @field_validator("policy_source", "selection_reason", mode="before")
    @classmethod
    def _text(cls, value: object, info) -> str:
        return safe_text(value, field=info.field_name, maximum=128)

    @model_validator(mode="after")
    def _gate_shape(self) -> ModelInvocationContext:
        if tuple(item.name for item in self.gates) != _GATE_NAMES:
            raise ValueError("invocation gate set is invalid")
        return self


class ModelInvocationReceipt(ModelGatewayContract):
    budget_phase: Literal["PREPARED", "COUNTER_INTENT", "COUNTER_OBSERVED", "GENERATION_INTENT", "TERMINAL"] | None = None
    counter_admission_fingerprint: str | None = None
    counter_observation_fingerprint: str | None = None
    counter_status: Literal["NOT_ATTEMPTED", "DISPATCH_INTENT", "COUNTED", "HTTP_ERROR", "INVALID_RESPONSE", "OUTCOME_UNKNOWN"] | None = None
    counter_transport_send_count: int | None = Field(default=None, strict=True, ge=0, le=1)
    counter_response_observed: bool = False
    generation_transport_send_count: int | None = Field(default=None, strict=True, ge=0, le=1)
    total_external_request_count: int | None = Field(default=None, strict=True, ge=0, le=2)
    counter_http_status: int | None = Field(default=None, strict=True, ge=100, le=599)
    counter_input_tokens: int | None = Field(default=None, strict=True, ge=0)
    schema_version: int = Field(default=1, ge=1, le=7)
    budget_envelope_id: str | None = None
    budget_evidence_fingerprint: str | None = None
    budget_reservation_id: str | None = None
    budget_reconciliation_state: str | None = None
    request_id: str
    trace_context: ModelInvocationTraceContext
    project_id: str
    stage_id: EngineeringStage
    invocation_mode: InvocationMode | None = None
    authority_reference: TaskAuthorityReference | None = None
    task_type: str
    policy_source: str
    selection_mode: SelectionMode
    selection_reason: str
    selected_model: BoundModelIdentity | None = None
    connection_id: str | None = None
    candidate_selection_evidence: CandidateSelectionEvidence | None = None
    auto_selection_evidence: AutoSelectionEvidence | None = None
    credential_revision: str | None = None
    required_capabilities: tuple[ModelCapability, ...] = ()
    input_modalities: tuple[str, ...] = ("text",)
    image_count: int = Field(default=0, ge=0, le=8)
    gates: tuple[ModelInvocationGate, ...] = ()
    started_at: datetime
    completed_at: datetime
    latency_ms: float = Field(ge=0.0)
    provider_request_id: str | None = None
    http_status: int | None = Field(default=None, ge=100, le=599)
    result: InvocationStatus
    error_code: str | None = None
    provider_request_count: int = Field(default=0, ge=0)
    gateway_call_count: int | None = Field(default=None, ge=0, le=1)
    gateway_response_observed: bool = False
    transport_send_count: int | None = Field(default=None, ge=0)
    usage: ModelUsage | None = None
    fingerprint: str = "sha256:" + "0" * 64

    @field_validator("usage", mode="before")
    @classmethod
    def _usage(cls, value: object) -> ModelUsage | None:
        return None if value is None else ModelUsage.model_validate(copy.deepcopy(value))

    @field_validator("request_id", "project_id", "task_type", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("policy_source", "selection_reason", "error_code", mode="before")
    @classmethod
    def _safe_text(cls, value: object, info) -> str | None:
        return None if value is None else safe_text(value, field=info.field_name, maximum=128)

    @field_validator("provider_request_id", mode="before")
    @classmethod
    def _provider_request_id(cls, value: object) -> str | None:
        return None if value is None else identifier(value, field="provider_request_id")

    @field_validator("connection_id", mode="before")
    @classmethod
    def _connection_id(cls, value: object) -> str | None:
        return None if value is None else identifier(value, field="connection_id")

    @field_validator("input_modalities", mode="before")
    @classmethod
    def _input_modalities(cls, value: object) -> tuple[str, ...]:
        if isinstance(value, list):
            value = tuple(value)
        if type(value) is not tuple:
            raise TypeError("input_modalities must be a tuple")
        checked = tuple(identifier(item, field="input_modality").casefold() for item in value)
        if not checked or len(checked) != len(set(checked)) or set(checked) - {"text", "image"}:
            raise ValueError("input_modalities are invalid")
        return checked

    @field_validator("started_at", "completed_at", mode="before")
    @classmethod
    def _times(cls, value: object, info) -> datetime:
        if isinstance(value, str):
            try:
                value = datetime.fromisoformat(value)
            except ValueError as error:
                raise ValueError(f"{info.field_name} is invalid") from error
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValueError(f"{info.field_name} is invalid")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _verify(self) -> ModelInvocationReceipt:
        if self.schema_version >= 5:
            if self.invocation_mode is None or (self.invocation_mode is InvocationMode.TASK) != (self.authority_reference is not None):
                raise ValueError("receipt authority binding is invalid")
            if self.authority_reference is not None and self.authority_reference.project_id != self.project_id:
                raise ValueError("receipt authority project mismatch")
            if self.result is InvocationStatus.SUCCESS and (self.gateway_call_count != 1 or not self.gateway_response_observed):
                raise ValueError("successful v5 receipt requires observed gateway response")
            if self.provider_request_count != 0:
                raise ValueError("v5 receipt cannot claim a confirmed provider send")
        elif self.invocation_mode is not None or self.authority_reference is not None or self.gateway_call_count is not None or self.gateway_response_observed or self.transport_send_count is not None:
            raise ValueError("legacy receipt cannot contain v5 fields")
        budget_fields = (self.budget_envelope_id, self.budget_evidence_fingerprint, self.budget_reservation_id)
        if self.schema_version < 6 and (any(v is not None for v in budget_fields) or self.budget_reconciliation_state is not None):
            raise ValueError("legacy receipt cannot contain budget fields")
        if self.schema_version == 6:
            if self.invocation_mode is not InvocationMode.TASK:
                raise ValueError("budget receipt requires TASK")
            if any(v is not None for v in budget_fields) and not all(v is not None for v in budget_fields):
                raise ValueError("partial budget receipt binding")
            if self.result is InvocationStatus.SUCCESS and not all(v is not None for v in budget_fields):
                raise ValueError("successful budget receipt binding missing")
            if self.budget_envelope_id is not None:
                identifier(self.budget_envelope_id, field="budget_envelope_id")
                identifier(self.budget_reservation_id, field="budget_reservation_id")
                validate_fingerprint(self.budget_evidence_fingerprint)
            if self.budget_reconciliation_state not in {"REPORTED", "ESTIMATED", "UNKNOWN", "BOUND_VIOLATION"}:
                raise ValueError("budget reconciliation state invalid")
            if self.budget_reconciliation_state != "UNKNOWN" and self.budget_envelope_id is None:
                raise ValueError("budget reconciliation requires persisted proof")
            if self.budget_reconciliation_state == "BOUND_VIOLATION" and self.result is InvocationStatus.SUCCESS:
                raise ValueError("budget violation cannot be successful")
        if self.schema_version < 7:
            if any(getattr(self, name) is not None for name in _BUNDLE_ROLE_FIELDS if name != "counter_response_observed") or self.counter_response_observed:
                raise ValueError("legacy receipt cannot contain bundle fields")
        else:
            if self.invocation_mode is not InvocationMode.TASK:
                raise ValueError("bundle receipt requires TASK")
            _validate_bundle_roles(self)
            if self.budget_reconciliation_state not in {"REPORTED", "ESTIMATED", "UNKNOWN", "BOUND_VIOLATION"}:
                raise ValueError("bundle reconciliation state is invalid")
            if self.result is InvocationStatus.SUCCESS and (
                self.budget_phase != "TERMINAL" or self.counter_status != "COUNTED"
                or self.counter_transport_send_count != 1
                or self.generation_transport_send_count != 1
                or self.budget_evidence_fingerprint is None
            ):
                raise ValueError("successful bundle receipt requires both operations")
            if self.transport_send_count != self.generation_transport_send_count:
                raise ValueError("legacy send projection retains generation scope")
            if self.budget_reconciliation_state == "BOUND_VIOLATION" and self.result is InvocationStatus.SUCCESS:
                raise ValueError("bundle budget violation cannot succeed")
        correlation = self.trace_context.correlation
        if correlation is not None and (
            correlation.request_id != self.request_id
            or correlation.project_id != self.project_id
        ):
            raise ValueError("invocation receipt correlation binding mismatch")
        if self.completed_at < self.started_at:
            raise ValueError("receipt completed_at is before started_at")
        if tuple(item.name for item in self.gates) != _GATE_NAMES:
            raise ValueError("receipt gate set is invalid")
        if self.image_count > 0 and "image" not in self.input_modalities:
            raise ValueError("image receipt modality is missing")
        if self.image_count == 0 and "image" in self.input_modalities:
            raise ValueError("image receipt count is missing")
        if self.result is InvocationStatus.SUCCESS and self.error_code is not None:
            raise ValueError("successful receipt cannot contain error")
        if self.candidate_selection_evidence is not None and (
            self.selection_mode is not SelectionMode.CANDIDATE_GROUP
            or self.selected_model != self.candidate_selection_evidence.selected_candidate
        ):
            raise ValueError("candidate selection receipt binding is invalid")
        if self.auto_selection_evidence is not None and (
            self.schema_version not in {2, 3, 4, 5, 6, 7}
            or self.selection_mode is not SelectionMode.AUTO
            or self.selected_model != self.auto_selection_evidence.selected_candidate
            or self.selection_reason != self.auto_selection_evidence.selection_reason
            or self.candidate_selection_evidence is not None
        ):
            raise ValueError("auto selection receipt binding is invalid")
        if self.schema_version == 2 and self.auto_selection_evidence is None:
            raise ValueError("auto selection receipt evidence is missing")
        if self.schema_version < 3 and self.connection_id is not None:
            raise ValueError("legacy invocation receipt cannot contain connection provenance")
        if self.usage is not None and self.schema_version < 4:
            raise ValueError("legacy invocation receipt cannot contain usage")
        excluded = {"fingerprint"}
        if self.candidate_selection_evidence is None:
            excluded.add("candidate_selection_evidence")
        if self.schema_version == 1:
            excluded.add("auto_selection_evidence")
        if self.schema_version < 3:
            excluded.add("connection_id")
        if self.schema_version < 4:
            excluded.add("usage")
        if self.schema_version < 5:
            excluded.update({"invocation_mode", "authority_reference", "gateway_call_count", "gateway_response_observed", "transport_send_count"})
        expected = canonical_fingerprint(self, exclude=excluded)
        if self.fingerprint == "sha256:" + "0" * 64:
            object.__setattr__(self, "fingerprint", expected)
        elif self.fingerprint != expected:
            raise ValueError("invocation receipt fingerprint mismatch")
        return self


    @model_serializer(mode="wrap")
    def _legacy_json(self, handler):
        value = handler(self)
        if self.schema_version < 7:
            for name in _BUNDLE_ROLE_FIELDS:
                value.pop(name, None)
        if self.schema_version < 6:
            for name in ("budget_envelope_id", "budget_evidence_fingerprint", "budget_reservation_id", "budget_reconciliation_state"):
                value.pop(name, None)
        return value


class ModelInvocationResult(ModelGatewayContract):
    status: InvocationStatus
    response: ModelResponse | None = None
    context: ModelInvocationContext
    receipt: ModelInvocationReceipt | None = None
    error_code: str | None = None
    detail: str | None = None
    provider_request_count: int = Field(default=0, ge=0)

    @field_validator("error_code", mode="before")
    @classmethod
    def _error_code(cls, value: object) -> str | None:
        return None if value is None else identifier(value, field="error_code")

    @field_validator("detail", mode="before")
    @classmethod
    def _result_detail(cls, value: object) -> str | None:
        return None if value is None else safe_text(value, field="detail", maximum=256)


__all__ = (
    "AttemptStatus",
    "ConditionalGenerationCeilingV1",
    "CounterAdmissionV1",
    "CounterObservationV1",
    "GateStatus",
    "InvocationStatus",
    "ModelExecutionSnapshot",
    "ModelInvocationAttempt",
    "ModelInvocationContext",
    "ModelInvocationGate",
    "ModelInvocationGenerationOptions",
    "ModelInvocationPayload",
    "ModelInvocationReceipt",
    "ModelInvocationRequest",
    "ModelInvocationResult",
    "ModelInvocationStatus",
    "ModelInvocationTraceContext",
    "SingleInvocationBudgetEnvelopeV2",
)
