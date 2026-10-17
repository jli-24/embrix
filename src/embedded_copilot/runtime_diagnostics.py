"""脱敏的 ApplicationRuntime 边界诊断。

该模块只记录固定阶段、状态、异常类型和受限 reason code，不记录请求正文、
项目内容、路径或任何运行时对象。
"""

from __future__ import annotations

import logging
import hashlib
from enum import Enum


_LOGGER = logging.getLogger(__name__)
APPLICATION_RUNTIME_TELEMETRY_EVENT = "application_runtime_boundary_telemetry"
APPLICATION_RUNTIME_STAGES = (
    "A0_REQUEST_ENTER",
    "A1_CONVERSATION_LOAD",
    "A2_PROJECT_CONTEXT_BIND",
    "A3_REQUIREMENT_CONTEXT_BIND",
    "A4_TASK_MODE_RESOLVE",
    "A5_ANSWER_BINDING_CREATE",
    "A6_PROMPT_REQUEST_BUILD",
    "A7_PROMPT_BUILDER_ENTER",
)
PROMPT_PIPELINE_TELEMETRY_EVENT = "prompt_request_boundary_telemetry"
PROMPT_PIPELINE_STAGES = (
    "P0_ENGINEERING_QA_SERVICE_ENTER",
    "P1_TASK_REQUEST_CONSTRUCTION_START",
    "P2_REQUIREMENT_CONTEXT_SERIALIZE",
    "P3_GREENFIELD_PROMPT_SELECT",
    "P4_PROMPT_BUILDER_CALL",
    "P5_MODEL_REQUEST_CREATED",
)
APPLICATION_RUNTIME_OUTER_TELEMETRY_EVENT = "application_runtime_outer_boundary_telemetry"
APPLICATION_RUNTIME_OUTER_STAGES = (
    "X0_ANSWER_ENTER",
    "X1_AFTER_BINDING",
    "X2_BEFORE_ENGINEERING_QA_CALL",
    "X3_ENGINEERING_QA_EXCEPTION_CAUGHT",
    "X4_OUTER_EXCEPTION_MAPPING",
)
ENGINEERING_QA_TELEMETRY_EVENT = "engineering_qa_exception_boundary_telemetry"
ENGINEERING_QA_STAGES = (
    "Q0_ENGINEERING_QA_ENTER",
    "Q1_PROMPT_SERVICE_RESOLVE",
    "Q2_PROMPT_BUILD_REQUEST",
    "Q3_MODEL_REQUEST_CONSTRUCTION",
    "Q4_ENGINEERING_QA_EXCEPTION",
)
BINDING_ENGINEERING_QA_TELEMETRY_EVENT = "binding_engineering_qa_boundary_telemetry"
BINDING_ENGINEERING_QA_STAGES = (
    "B0_BINDING_ANSWER_CALL_START",
    "B1_ENGINEERING_QA_SERVICE_RESOLVE_START",
    "B2_ENGINEERING_QA_SERVICE_RESOLVE_END",
    "B3_ENGINEERING_QA_METHOD_CALL_START",
    "B4_ENGINEERING_QA_EXCEPTION",
)
_STATUSES = {"PASS", "FAIL", "NOT_REACHED"}
_SAFE_REASON_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")


def _safe_boundary_label(value: object) -> str:
    if not isinstance(value, str):
        return "-"
    checked = value.strip().upper()
    if (
        not checked
        or len(checked) > 96
        or any(char not in _SAFE_REASON_CHARS for char in checked)
    ):
        return "-"
    return checked


def _safe_reason_code(error: BaseException | None, explicit: object = None) -> str:
    raw = explicit
    if raw is None and error is not None:
        raw = getattr(error, "reason_code", None)
    if isinstance(raw, Enum):
        raw = raw.value
    if not isinstance(raw, str):
        return "-"
    value = raw.strip().upper()
    if not value or len(value) > 64 or any(char not in _SAFE_REASON_CHARS for char in value):
        return "-"
    return value


def record_application_runtime_stage(
    stage: str,
    status: str,
    *,
    error: BaseException | None = None,
    reason_code: object = None,
    exception_layer: str | None = None,
    exception_stage: str | None = None,
) -> None:
    """Emit one fixed-shape, content-free runtime boundary event."""

    if stage not in APPLICATION_RUNTIME_STAGES:
        return
    normalized_status = status if status in _STATUSES else "FAIL"
    diagnostics = _diagnostic_fields(
        error,
        exception_layer=exception_layer,
        exception_stage=exception_stage or stage,
    )
    _LOGGER.info(
        APPLICATION_RUNTIME_TELEMETRY_EVENT,
        extra={
            "event_name": APPLICATION_RUNTIME_TELEMETRY_EVENT,
            "application_runtime_stage": stage,
            "telemetry_status": normalized_status,
            "exception_type": "-" if error is None else type(error).__name__,
            **diagnostics,
            "reason_code": _safe_reason_code(error, reason_code),
        },
    )


def record_application_runtime_not_reached(stages: tuple[str, ...]) -> None:
    for stage in stages:
        record_application_runtime_stage(stage, "NOT_REACHED")


def _exception_type_hash(error: BaseException | None) -> str:
    if error is None:
        return "-"
    qualified_name = f"{type(error).__module__}.{type(error).__qualname__}"
    return hashlib.sha256(qualified_name.encode("utf-8")).hexdigest()


def _traceback_fingerprint(error: BaseException | None) -> str:
    """Hash traceback structure without retaining messages, paths or locals."""

    if error is None:
        return "-"
    frames: list[str] = []
    current = error.__traceback__
    while current is not None:
        frame = current.tb_frame
        module = str(frame.f_globals.get("__name__", "-"))
        frames.append(f"{module}|{frame.f_code.co_name}|{current.tb_lineno}")
        current = current.tb_next
    if not frames:
        return "-"
    return hashlib.sha256("\n".join(frames).encode("utf-8")).hexdigest()


def _diagnostic_source(error: BaseException | None) -> BaseException | None:
    """Find the first chained exception carrying boundary metadata."""

    current = error
    seen: set[int] = set()
    leaf = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        leaf = current
        if any(
            getattr(current, field, None)
            for field in (
                "original_exception_type",
                "original_exception_hash",
                "traceback_fingerprint",
            )
        ):
            return current
        current = current.__cause__ or current.__context__
    return leaf


def exception_diagnostics(
    error: BaseException | None,
    *,
    exception_layer: str | None = None,
    exception_stage: str | None = None,
) -> dict[str, str]:
    """Return fixed, content-free original-exception metadata."""

    source = _diagnostic_source(error)
    original_type = getattr(error, "original_exception_type", None)
    if not isinstance(original_type, str) or not original_type:
        original_type = getattr(source, "original_exception_type", None)
    if not isinstance(original_type, str) or not original_type:
        original_type = "-" if source is None else type(source).__name__
    original_hash = getattr(error, "original_exception_hash", None)
    if not isinstance(original_hash, str) or not original_hash:
        original_hash = getattr(source, "original_exception_hash", None)
    if not isinstance(original_hash, str) or not original_hash:
        original_hash = _exception_type_hash(source)
    traceback_fingerprint = getattr(error, "traceback_fingerprint", None)
    if not isinstance(traceback_fingerprint, str) or not traceback_fingerprint:
        traceback_fingerprint = getattr(source, "traceback_fingerprint", None)
    if not isinstance(traceback_fingerprint, str) or not traceback_fingerprint:
        traceback_fingerprint = _traceback_fingerprint(source)
    layer = getattr(error, "exception_layer", None)
    if not isinstance(layer, str) or not layer:
        layer = getattr(source, "exception_layer", None)
    if not isinstance(layer, str) or not layer:
        layer = exception_layer
    stage = getattr(error, "exception_stage", None)
    if not isinstance(stage, str) or not stage:
        stage = getattr(source, "exception_stage", None)
    if not isinstance(stage, str) or not stage:
        stage = exception_stage
    return {
        "original_exception_type": original_type,
        "original_exception_hash": original_hash,
        "exception_layer": _safe_boundary_label(layer),
        "exception_stage": _safe_boundary_label(stage),
        "traceback_fingerprint": traceback_fingerprint,
    }


def attach_exception_diagnostics(
    target: BaseException,
    error: BaseException | None = None,
    *,
    exception_layer: str | None = None,
    exception_stage: str | None = None,
) -> BaseException:
    """Attach only safe original-exception metadata to a wrapper exception."""

    source = target if error is None else error
    values = exception_diagnostics(
        source,
        exception_layer=exception_layer,
        exception_stage=exception_stage,
    )
    for field, value in values.items():
        try:
            setattr(target, field, value)
        except Exception:  # pragma: no cover - defensive for foreign exceptions
            continue
    return target


def _diagnostic_fields(
    error: BaseException | None,
    *,
    exception_layer: str | None = None,
    exception_stage: str | None = None,
) -> dict[str, str]:
    values = exception_diagnostics(
        error,
        exception_layer=exception_layer,
        exception_stage=exception_stage,
    )
    values["exception_type_hash"] = _exception_type_hash(error)
    return values


def record_prompt_pipeline_stage(
    stage: str,
    status: str,
    *,
    error: BaseException | None = None,
    reason_code: object = None,
    engineering_qa_service_available: bool | None = None,
    prompt_module_loaded: bool | None = None,
    greenfield_prompt_available: bool | None = None,
    model_request_schema_valid: bool | None = None,
    context_projection_valid: bool | None = None,
    constraints_valid: bool | None = None,
    exception_layer: str | None = None,
    exception_stage: str | None = None,
) -> None:
    """Emit one fixed-shape prompt-construction event without content fields."""

    if stage not in PROMPT_PIPELINE_STAGES:
        return
    normalized_status = status if status in _STATUSES else "FAIL"
    diagnostics = _diagnostic_fields(
        error,
        exception_layer=exception_layer,
        exception_stage=exception_stage or stage,
    )
    _LOGGER.info(
        PROMPT_PIPELINE_TELEMETRY_EVENT,
        extra={
            "event_name": PROMPT_PIPELINE_TELEMETRY_EVENT,
            "stage": stage,
            "status": normalized_status,
            "prompt_pipeline_stage": stage,
            "telemetry_status": normalized_status,
            **diagnostics,
            "reason_code": _safe_reason_code(error, reason_code),
            "engineering_qa_service_available": engineering_qa_service_available,
            "prompt_module_loaded": prompt_module_loaded,
            "greenfield_prompt_available": greenfield_prompt_available,
            "model_request_schema_valid": model_request_schema_valid,
            "context_projection_valid": context_projection_valid,
            "constraints_valid": constraints_valid,
        },
    )


def record_prompt_pipeline_not_reached(stages: tuple[str, ...]) -> None:
    for stage in stages:
        record_prompt_pipeline_stage(stage, "NOT_REACHED")


def record_application_runtime_outer_stage(
    stage: str,
    status: str,
    *,
    error: BaseException | None = None,
    reason_code: object = None,
    exception_layer: str | None = None,
    exception_stage: str | None = None,
) -> None:
    """Emit fixed-shape diagnostics for the ApplicationRuntime outer boundary."""

    if stage not in APPLICATION_RUNTIME_OUTER_STAGES:
        return
    normalized_status = status if status in _STATUSES else "FAIL"
    diagnostics = _diagnostic_fields(
        error,
        exception_layer=exception_layer,
        exception_stage=exception_stage or stage,
    )
    _LOGGER.info(
        APPLICATION_RUNTIME_OUTER_TELEMETRY_EVENT,
        extra={
            "event_name": APPLICATION_RUNTIME_OUTER_TELEMETRY_EVENT,
            "stage": stage,
            "status": normalized_status,
            "application_runtime_outer_stage": stage,
            "telemetry_status": normalized_status,
            **diagnostics,
            "reason_code": _safe_reason_code(error, reason_code),
        },
    )


def record_application_runtime_outer_not_reached(
    stages: tuple[str, ...],
) -> None:
    for stage in stages:
        record_application_runtime_outer_stage(stage, "NOT_REACHED")


def record_engineering_qa_stage(
    stage: str,
    status: str,
    *,
    error: BaseException | None = None,
    reason_code: object = None,
    engineering_qa_service_instance_available: bool | None = None,
    prompt_service_dependency_available: bool | None = None,
    prompt_builder_dependency_available: bool | None = None,
    request_builder_dependency_available: bool | None = None,
    model_request_schema_valid: bool | None = None,
    exception_layer: str | None = None,
    exception_stage: str | None = None,
) -> None:
    """Emit fixed-shape EngineeringQA exception-boundary diagnostics."""

    if stage not in ENGINEERING_QA_STAGES:
        return
    normalized_status = status if status in _STATUSES else "FAIL"
    diagnostics = _diagnostic_fields(
        error,
        exception_layer=exception_layer,
        exception_stage=exception_stage or stage,
    )
    _LOGGER.info(
        ENGINEERING_QA_TELEMETRY_EVENT,
        extra={
            "event_name": ENGINEERING_QA_TELEMETRY_EVENT,
            "stage": stage,
            "status": normalized_status,
            "engineering_qa_stage": stage,
            "telemetry_status": normalized_status,
            **diagnostics,
            "reason_code": _safe_reason_code(error, reason_code),
            "engineering_qa_service_instance_available": engineering_qa_service_instance_available,
            "prompt_service_dependency_available": prompt_service_dependency_available,
            "prompt_builder_dependency_available": prompt_builder_dependency_available,
            "request_builder_dependency_available": request_builder_dependency_available,
            "model_request_schema_valid": model_request_schema_valid,
        },
    )


def record_engineering_qa_not_reached(stages: tuple[str, ...]) -> None:
    for stage in stages:
        record_engineering_qa_stage(stage, "NOT_REACHED")


def record_binding_engineering_qa_stage(
    stage: str,
    status: str,
    *,
    error: BaseException | None = None,
    reason_code: object = None,
    binding_service_instance_exists: bool | None = None,
    service_constructor_completed: bool | None = None,
    service_method_exists: bool | None = None,
    method_signature_compatible: bool | None = None,
    dependency_container_resolved: bool | None = None,
    exception_layer: str | None = None,
    exception_stage: str | None = None,
) -> None:
    """Emit fixed-shape Binding -> EngineeringQA diagnostics."""

    if stage not in BINDING_ENGINEERING_QA_STAGES:
        return
    normalized_status = status if status in _STATUSES else "FAIL"
    diagnostics = _diagnostic_fields(
        error,
        exception_layer=exception_layer,
        exception_stage=exception_stage or stage,
    )
    _LOGGER.info(
        BINDING_ENGINEERING_QA_TELEMETRY_EVENT,
        extra={
            "event_name": BINDING_ENGINEERING_QA_TELEMETRY_EVENT,
            "stage": stage,
            "status": normalized_status,
            "binding_engineering_qa_stage": stage,
            "telemetry_status": normalized_status,
            **diagnostics,
            "reason_code": _safe_reason_code(error, reason_code),
            "binding_service_instance_exists": binding_service_instance_exists,
            "service_constructor_completed": service_constructor_completed,
            "service_method_exists": service_method_exists,
            "method_signature_compatible": method_signature_compatible,
            "dependency_container_resolved": dependency_container_resolved,
        },
    )


def record_binding_engineering_qa_not_reached(
    stages: tuple[str, ...],
) -> None:
    for stage in stages:
        record_binding_engineering_qa_stage(stage, "NOT_REACHED")


__all__ = (
    "APPLICATION_RUNTIME_STAGES",
    "APPLICATION_RUNTIME_TELEMETRY_EVENT",
    "record_application_runtime_not_reached",
    "record_application_runtime_stage",
    "attach_exception_diagnostics",
    "exception_diagnostics",
    "PROMPT_PIPELINE_STAGES",
    "PROMPT_PIPELINE_TELEMETRY_EVENT",
    "record_prompt_pipeline_not_reached",
    "record_prompt_pipeline_stage",
    "APPLICATION_RUNTIME_OUTER_STAGES",
    "APPLICATION_RUNTIME_OUTER_TELEMETRY_EVENT",
    "record_application_runtime_outer_not_reached",
    "record_application_runtime_outer_stage",
    "ENGINEERING_QA_STAGES",
    "ENGINEERING_QA_TELEMETRY_EVENT",
    "record_engineering_qa_not_reached",
    "record_engineering_qa_stage",
    "BINDING_ENGINEERING_QA_STAGES",
    "BINDING_ENGINEERING_QA_TELEMETRY_EVENT",
    "record_binding_engineering_qa_not_reached",
    "record_binding_engineering_qa_stage",
)
