from __future__ import annotations

import copy
import hashlib
from collections import deque
from datetime import UTC, datetime

from embedded_copilot.execution_worker.contracts import SimulationOperation, TestOperation
from embedded_copilot.execution_worker.ports import AdapterResult, ExecutionContext

from .contracts import (
    PreparedSimulation,
    SimulationAssertionResult,
    SimulationBackend,
    SimulationBackendCapability,
    SimulationCapabilityStatus,
    SimulationExecutionMode,
    SimulationFailureClass,
    SimulationRequest,
    SimulationResult,
    SimulationStatus,
    validate_request,
    validate_result,
)
from .exceptions import SimulationUnavailable
from .ports import (
    PreparedSimulationStorePort,
    SimulationAdapterExecutionContext,
    SimulationAdapterPort,
    SimulationClockPort,
    SimulationResultStorePort,
)


class _SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class FakeSimulationAdapter:
    """Deterministic test-only backend. It is never a real simulator claim."""

    __slots__ = ("_clock", "_outcomes")

    def __init__(
        self,
        *,
        clock: SimulationClockPort | None = None,
        outcomes: tuple[tuple[SimulationStatus, SimulationFailureClass, str], ...] = (),
    ) -> None:
        self._clock = clock or _SystemClock()
        self._outcomes = deque(outcomes)

    def probe(self) -> SimulationBackendCapability:
        return SimulationBackendCapability.create(
            backend=SimulationBackend.FAKE,
            status=SimulationCapabilityStatus.VERIFIED,
            version="test-fixture-1",
            execution_mode=SimulationExecutionMode.TEST_ONLY,
            supported_targets=("STM32F446RE", "ESP32-S3"),
            reason_code="FAKE_TEST_EXECUTION_VERIFIED",
        )

    def prepare(self, request: SimulationRequest) -> PreparedSimulation:
        checked = validate_request(request)
        digest = hashlib.sha256(checked.fingerprint.encode("utf-8")).hexdigest()[:48]
        return PreparedSimulation.create(
            prepared_id=f"prepared-{digest}",
            request=checked,
            session_model="ADAPTER_INTERNAL_EPHEMERAL",
        )

    def execute(
        self,
        prepared: PreparedSimulation,
        execution_context: SimulationAdapterExecutionContext,
    ) -> SimulationResult:
        checked = PreparedSimulation.model_validate(
            copy.deepcopy(prepared.model_dump(mode="python"))
        )
        status, failure_class, reason_code = (
            self._outcomes.popleft()
            if self._outcomes
            else (
                SimulationStatus.PASS,
                SimulationFailureClass.NONE,
                "SIMULATION_ASSERTIONS_PASSED",
            )
        )
        assertion_results: tuple[SimulationAssertionResult, ...] = ()
        if status in {
            SimulationStatus.PASS,
            SimulationStatus.FAIL,
            SimulationStatus.TIMEOUT,
        }:
            failed_first = failure_class is SimulationFailureClass.ENGINEERING
            assertion_results = tuple(
                SimulationAssertionResult.create(
                    assertion_id=assertion.assertion_id,
                    kind=assertion.kind,
                    passed=not (failed_first and index == 0),
                    observed_summary=(
                        "expected bounded observation was not produced"
                        if failed_first and index == 0
                        else "bounded assertion observation matched"
                    ),
                    evidence_reference=(
                        f"simulation-evidence:{checked.request.simulation_id}:{index}"
                    ),
                )
                for index, assertion in enumerate(checked.request.assertions)
            )
        now = self._clock.now()
        return SimulationResult.create(
            simulation_id=checked.request.simulation_id,
            operation_id=execution_context.operation_id,
            tenant_id=checked.request.tenant_id,
            project_id=checked.request.project_id,
            backend=checked.request.backend,
            status=status,
            failure_class=failure_class,
            reason_code=reason_code,
            started_at=now,
            finished_at=now,
            assertion_results=assertion_results,
            artifact_references=(
                f"artifact:{checked.request.firmware_artifact.artifact_id}",
            ),
            telemetry_references=(f"telemetry:{checked.request.simulation_id}:uart0",)
            if assertion_results
            else (),
            diagnostic_summary="deterministic offline fake simulation result",
            request_fingerprint=checked.request.fingerprint,
        )

    def cleanup(
        self,
        prepared: PreparedSimulation,
        execution_context: SimulationAdapterExecutionContext,
    ) -> None:
        return None


class _DisabledSimulationAdapter:
    __slots__ = ("_backend", "_mode", "_reason")

    def __init__(
        self,
        *,
        backend: SimulationBackend,
        execution_mode: SimulationExecutionMode,
        reason_code: str,
    ) -> None:
        self._backend = backend
        self._mode = execution_mode
        self._reason = reason_code

    def probe(self) -> SimulationBackendCapability:
        return SimulationBackendCapability.create(
            backend=self._backend,
            status=SimulationCapabilityStatus.UNAVAILABLE,
            version=None,
            execution_mode=self._mode,
            supported_targets=(),
            reason_code=self._reason,
        )

    def prepare(self, request: SimulationRequest) -> PreparedSimulation:
        raise SimulationUnavailable(self._reason)

    def execute(
        self,
        prepared: PreparedSimulation,
        execution_context: SimulationAdapterExecutionContext,
    ) -> SimulationResult:
        raise SimulationUnavailable(self._reason)

    def cleanup(
        self,
        prepared: PreparedSimulation,
        execution_context: SimulationAdapterExecutionContext,
    ) -> None:
        return None


class _DisabledRenodeSimulationAdapter(_DisabledSimulationAdapter):
    def __init__(self) -> None:
        super().__init__(
            backend=SimulationBackend.RENODE,
            execution_mode=SimulationExecutionMode.LOCAL_PROCESS,
            reason_code="RENODE_NOT_INSTALLED",
        )


class QemuSimulationAdapter(_DisabledSimulationAdapter):
    def __init__(self) -> None:
        super().__init__(
            backend=SimulationBackend.QEMU,
            execution_mode=SimulationExecutionMode.LOCAL_PROCESS,
            reason_code="QEMU_NOT_AVAILABLE",
        )


class WokwiSimulationAdapter(_DisabledSimulationAdapter):
    def __init__(self) -> None:
        super().__init__(
            backend=SimulationBackend.WOKWI,
            execution_mode=SimulationExecutionMode.EXTERNAL_CLOUD,
            reason_code="WOKWI_NOT_CONFIGURED",
        )


from .renode import RenodeSimulationAdapter


class SimulationBackedTestAdapter:
    """Compatibility bridge for the canonical ExecutionWorker simulation path.

    ``SimulationOperation`` is the production contract.  The ``TestOperation``
    branch remains only for the existing offline foundation fixtures; both
    branches execute through the one canonical worker and this adapter does not
    create a second worker or runtime loop.
    """

    __slots__ = ("_adapters", "_clock", "_prepared", "_results")

    def __init__(
        self,
        *,
        adapters: tuple[SimulationAdapterPort, ...],
        prepared_store: PreparedSimulationStorePort,
        result_store: SimulationResultStorePort,
        clock: SimulationClockPort | None = None,
    ) -> None:
        if (
            type(adapters) is not tuple
            or not adapters
            or any(not isinstance(item, SimulationAdapterPort) for item in adapters)
        ):
            raise TypeError("simulation adapters are invalid")
        capabilities = tuple(item.probe() for item in adapters)
        if len({item.backend for item in capabilities}) != len(capabilities):
            raise ValueError("simulation backend adapters must be unique")
        if not isinstance(prepared_store, PreparedSimulationStorePort):
            raise TypeError("prepared simulation store is invalid")
        if not isinstance(result_store, SimulationResultStorePort):
            raise TypeError("simulation result store is invalid")
        self._adapters = dict(
            zip((item.backend for item in capabilities), adapters, strict=True)
        )
        self._prepared = prepared_store
        self._results = result_store
        self._clock = clock or _SystemClock()

    def execute(
        self, operation: TestOperation | SimulationOperation, context: ExecutionContext
    ) -> AdapterResult:
        if type(operation).__name__ not in {"TestOperation", "SimulationOperation"}:
            return AdapterResult("UNAVAILABLE", "simulation test operation invalid")
        prepared_id = getattr(operation, "prepared_id", None) or getattr(operation, "test_profile_id", None)
        prepared = self._prepared.take(prepared_id)
        if prepared is None:
            return AdapterResult(
                "UNAVAILABLE", "ephemeral simulation preparation unavailable"
            )
        request = prepared.request
        execution_context = SimulationAdapterExecutionContext(
            operation_id=operation.operation_id,
            cancellation=context.cancellation,
            workspace_root=context.workspace_root,
            state_root=context.state_root,
            artifact_store=context.artifact_store,
        )
        if (
            operation.tenant_id != request.tenant_id
            or operation.project_id != request.project_id
            or operation.target_id != request.execution_binding.target_id
            or operation.workspace_fingerprint
            != request.execution_binding.workspace_fingerprint
            or operation.artifact != request.firmware_artifact
        ):
            result = self._infrastructure_result(
                prepared,
                operation.operation_id,
                "SIMULATION_OPERATION_BINDING_MISMATCH",
            )
        else:
            adapter = self._adapters.get(request.backend)
            if adapter is None:
                result = self._infrastructure_result(
                    prepared, operation.operation_id, "SIMULATION_BACKEND_UNAVAILABLE"
                )
            else:
                if self._is_cancelled(context):
                    result = self._cancelled_result(
                        prepared, operation.operation_id, "SIMULATION_CANCELLED"
                    )
                else:
                    try:
                        result = validate_result(
                            adapter.execute(copy.deepcopy(prepared), execution_context)
                        )
                        if not self._assertions_bind(prepared, result):
                            raise ValueError("simulation assertion binding mismatch")
                    except Exception:  # noqa: BLE001 - injected backend boundary
                        result = self._infrastructure_result(
                            prepared,
                            operation.operation_id,
                            "SIMULATION_ADAPTER_UNAVAILABLE",
                        )
                try:
                    adapter.cleanup(copy.deepcopy(prepared), execution_context)
                except Exception:  # noqa: BLE001 - cleanup is reported as infrastructure
                    if result.status is not SimulationStatus.CANCELLED:
                        result = self._infrastructure_result(
                            prepared,
                            operation.operation_id,
                            "SIMULATION_CLEANUP_FAILED",
                        )
        if (
            result.simulation_id != request.simulation_id
            or result.operation_id != operation.operation_id
            or result.tenant_id != operation.tenant_id
            or result.project_id != operation.project_id
            or result.backend is not request.backend
            or result.request_fingerprint != request.fingerprint
        ):
            result = self._infrastructure_result(
                prepared, operation.operation_id, "SIMULATION_RESULT_BINDING_MISMATCH"
            )
        self._results.put(operation.operation_id, result)
        evidence = tuple(
            dict.fromkeys(
                (
                    *result.artifact_references,
                    *result.telemetry_references,
                    *(item.evidence_reference for item in result.assertion_results),
                )
            )
        )
        status = self._adapter_status(result)
        return AdapterResult(
            status,
            result.diagnostic_summary,
            evidence_references=evidence,
        )

    @staticmethod
    def _is_cancelled(context: ExecutionContext) -> bool:
        try:
            return bool(context.cancellation.is_cancelled())
        except AttributeError:
            return False
        except Exception:  # noqa: BLE001 - cancellation boundary fails closed
            return True

    def _cancelled_result(
        self, prepared: PreparedSimulation, operation_id: str, reason_code: str
    ) -> SimulationResult:
        now = self._clock.now()
        request = prepared.request
        return SimulationResult.create(
            simulation_id=request.simulation_id,
            operation_id=operation_id,
            tenant_id=request.tenant_id,
            project_id=request.project_id,
            backend=request.backend,
            status=SimulationStatus.CANCELLED,
            failure_class=SimulationFailureClass.CANCELLED,
            reason_code=reason_code,
            started_at=now,
            finished_at=now,
            assertion_results=(),
            artifact_references=(f"artifact:{request.firmware_artifact.artifact_id}",),
            telemetry_references=(),
            diagnostic_summary="simulation operation was cancelled",
            request_fingerprint=request.fingerprint,
        )

    @staticmethod
    def _assertions_bind(
        prepared: PreparedSimulation, result: SimulationResult
    ) -> bool:
        expected = {item.assertion_id for item in prepared.request.assertions}
        observed = {item.assertion_id for item in result.assertion_results}
        if not observed.issubset(expected):
            return False
        return result.status is not SimulationStatus.PASS or observed == expected

    def _infrastructure_result(
        self, prepared: PreparedSimulation, operation_id: str, reason_code: str
    ) -> SimulationResult:
        now = self._clock.now()
        request = prepared.request
        return SimulationResult.create(
            simulation_id=request.simulation_id,
            operation_id=operation_id,
            tenant_id=request.tenant_id,
            project_id=request.project_id,
            backend=request.backend,
            status=SimulationStatus.INFRASTRUCTURE_ERROR,
            failure_class=SimulationFailureClass.INFRASTRUCTURE,
            reason_code=reason_code,
            started_at=now,
            finished_at=now,
            assertion_results=(),
            artifact_references=(f"artifact:{request.firmware_artifact.artifact_id}",),
            telemetry_references=(),
            diagnostic_summary="simulation execution failed closed at adapter boundary",
            request_fingerprint=request.fingerprint,
        )

    @staticmethod
    def _adapter_status(result: SimulationResult) -> str:
        if result.status is SimulationStatus.PASS:
            return "SUCCEEDED"
        if result.status is SimulationStatus.CANCELLED:
            return "CANCELLED"
        if result.status is SimulationStatus.INFRASTRUCTURE_ERROR:
            return "UNAVAILABLE"
        if result.status is SimulationStatus.TIMEOUT:
            return (
                "TIMED_OUT"
                if result.failure_class is SimulationFailureClass.INFRASTRUCTURE
                else "FAILED"
            )
        if result.status is SimulationStatus.FAIL:
            return "FAILED"
        return "UNAVAILABLE"


__all__ = (
    "FakeSimulationAdapter",
    "QemuSimulationAdapter",
    "RenodeSimulationAdapter",
    "SimulationBackedTestAdapter",
    "WokwiSimulationAdapter",
)
