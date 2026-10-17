from __future__ import annotations

import copy
import hashlib
from datetime import datetime

from embedded_copilot.execution_worker.contracts import (
    OperationReceipt,
    OperationStatus,
    OperationStatusView,
    SimulationOperation,
)

from .contracts import (
    PreparedSimulation,
    SimulationBackendCapability,
    SimulationCapabilityStatus,
    SimulationFailureClass,
    SimulationRequest,
    SimulationResult,
    SimulationStatus,
    validate_request,
    validate_result,
)
from .exceptions import SimulationRejected, SimulationUnavailable
from .ports import (
    PreparedSimulationStorePort,
    SimulationAdapterPort,
    SimulationClockPort,
    SimulationOperationPort,
    SimulationResultStorePort,
)


class SimulationService:
    """Simulation coordinator over the canonical controlled operation lifecycle."""

    __slots__ = ("_adapters", "_clock", "_operations", "_prepared", "_results")

    def __init__(
        self,
        *,
        adapters: tuple[SimulationAdapterPort, ...],
        operations: SimulationOperationPort,
        prepared_store: PreparedSimulationStorePort,
        result_store: SimulationResultStorePort,
        clock: SimulationClockPort,
    ) -> None:
        if (
            type(adapters) is not tuple
            or not adapters
            or any(not isinstance(item, SimulationAdapterPort) for item in adapters)
        ):
            raise TypeError("simulation adapters are invalid")
        capabilities = tuple(self._capability(item) for item in adapters)
        if len({item.backend for item in capabilities}) != len(capabilities):
            raise ValueError("simulation backend adapters must be unique")
        if not isinstance(operations, SimulationOperationPort):
            raise TypeError("simulation operation port is invalid")
        if not isinstance(prepared_store, PreparedSimulationStorePort):
            raise TypeError("prepared simulation store is invalid")
        if not isinstance(result_store, SimulationResultStorePort):
            raise TypeError("simulation result store is invalid")
        if not isinstance(clock, SimulationClockPort):
            raise TypeError("simulation clock is invalid")
        self._adapters = dict(
            zip((item.backend for item in capabilities), adapters, strict=True)
        )
        self._operations = operations
        self._prepared = prepared_store
        self._results = result_store
        self._clock = clock

    @staticmethod
    def _capability(adapter: SimulationAdapterPort) -> SimulationBackendCapability:
        value = adapter.probe()
        if type(value) is not SimulationBackendCapability:
            raise TypeError("simulation backend capability is invalid")
        return SimulationBackendCapability.model_validate(
            copy.deepcopy(value.model_dump(mode="python"))
        )

    def probe(self) -> tuple[SimulationBackendCapability, ...]:
        return tuple(
            sorted(
                (self._capability(item) for item in self._adapters.values()),
                key=lambda item: item.backend.value,
            )
        )

    def operation_for(self, request: SimulationRequest) -> SimulationOperation:
        checked = validate_request(request)
        binding = checked.execution_binding
        digest = hashlib.sha256(checked.fingerprint.encode("utf-8")).hexdigest()
        capability = self._capability(self._adapters[checked.backend])
        return SimulationOperation(
            operation_id=f"simulation-{digest[:48]}",
            tenant_id=checked.tenant_id,
            project_id=checked.project_id,
            requester_id=binding.requester_id,
            target_id=binding.target_id,
            target_fingerprint=binding.target_fingerprint,
            workspace_snapshot_id=binding.workspace_snapshot_id,
            workspace_fingerprint=binding.workspace_fingerprint,
            proposal_id=binding.proposal_id,
            proposal_fingerprint=binding.proposal_fingerprint,
            created_at=binding.created_at,
            expires_at=binding.expires_at,
            nonce=binding.nonce,
            simulation_id=checked.simulation_id,
            scenario_id=f"scenario-{digest[:32]}",
            scenario_fingerprint=checked.fingerprint,
            backend=checked.backend.value,
            backend_version=capability.version or "unknown",
            platform_descriptor=checked.platform_descriptor,
            prepared_id=f"simulation-profile-{digest[:48]}",
            request_fingerprint=checked.fingerprint,
            timeout_seconds=checked.timeout_seconds,
            artifact=checked.firmware_artifact,
        )

    def evaluate(self, request: SimulationRequest) -> SimulationResult:
        try:
            checked = validate_request(request)
        except Exception as error:
            raise SimulationRejected("simulation request rejected") from error
        adapter = self._adapters.get(checked.backend)
        if adapter is None:
            return self._infrastructure_result(
                checked, None, "SIMULATION_BACKEND_UNSUPPORTED"
            )
        try:
            capability = self._capability(adapter)
        except Exception:  # noqa: BLE001 - capability probe fails closed
            return self._infrastructure_result(
                checked, None, "SIMULATION_CAPABILITY_UNAVAILABLE"
            )
        if capability.status is SimulationCapabilityStatus.UNAVAILABLE:
            return self._infrastructure_result(checked, None, capability.reason_code)
        operation = self.operation_for(checked)
        try:
            status = self._operations.get_status(
                tenant_id=operation.tenant_id,
                project_id=operation.project_id,
                operation_id=operation.operation_id,
            )
        except Exception:  # noqa: BLE001 - operation lifecycle boundary
            return self._infrastructure_result(
                checked, operation.operation_id, "SIMULATION_OPERATION_UNAVAILABLE"
            )
        if status is not None:
            try:
                if type(status) is not OperationStatusView:
                    raise TypeError("operation status is invalid")
                status = OperationStatusView.model_validate(
                    copy.deepcopy(status.model_dump(mode="python"))
                )
            except Exception:  # noqa: BLE001 - malformed lifecycle state
                return self._infrastructure_result(
                    checked, operation.operation_id, "SIMULATION_OPERATION_UNAVAILABLE"
                )
        if status is None:
            try:
                prepared = adapter.prepare(copy.deepcopy(checked))
                if type(prepared) is not PreparedSimulation:
                    raise TypeError("prepared simulation is invalid")
                prepared = PreparedSimulation.model_validate(
                    copy.deepcopy(prepared.model_dump(mode="python"))
                )
                if prepared.request != checked:
                    raise ValueError("prepared simulation request mismatch")
                self._prepared.put(operation.prepared_id, prepared)
                status = self._operations.create(operation)
                if type(status) is not OperationStatusView:
                    raise TypeError("operation status is invalid")
                status = OperationStatusView.model_validate(
                    copy.deepcopy(status.model_dump(mode="python"))
                )
            except SimulationUnavailable:
                return self._infrastructure_result(
                    checked, operation.operation_id, "SIMULATION_ADAPTER_UNAVAILABLE"
                )
            except Exception as error:
                raise SimulationRejected("simulation operation rejected") from error
        if (
            status.tenant_id != checked.tenant_id
            or status.project_id != checked.project_id
            or status.operation_id != operation.operation_id
            or status.operation_type is not operation.operation_type
        ):
            raise SimulationRejected("simulation operation identity mismatch")
        if status.status is OperationStatus.WAIT_APPROVAL:
            return self._non_terminal(
                checked,
                operation.operation_id,
                SimulationStatus.PENDING,
                "SIMULATION_WAIT_APPROVAL",
            )
        if status.status in {
            OperationStatus.CREATED,
            OperationStatus.QUEUED,
            OperationStatus.CANCEL_REQUESTED,
            OperationStatus.CANCEL_PENDING,
        }:
            return self._non_terminal(
                checked,
                operation.operation_id,
                SimulationStatus.PENDING,
                f"SIMULATION_{status.status.value}",
            )
        if status.status is OperationStatus.RUNNING:
            return self._non_terminal(
                checked,
                operation.operation_id,
                SimulationStatus.RUNNING,
                "SIMULATION_RUNNING",
                started_at=status.updated_at,
            )
        try:
            receipt = self._operations.get_receipt(
                tenant_id=operation.tenant_id,
                project_id=operation.project_id,
                operation_id=operation.operation_id,
            )
            stored = self._results.get(operation.operation_id)
        except Exception:  # noqa: BLE001 - result store is an injected boundary
            return self._infrastructure_result(
                checked, operation.operation_id, "SIMULATION_RESULT_UNAVAILABLE"
            )
        if stored is None:
            if (
                status.status is OperationStatus.CANCELLED
                and type(receipt) is OperationReceipt
                and receipt.operation_id == operation.operation_id
                and receipt.tenant_id == checked.tenant_id
                and receipt.project_id == checked.project_id
                and receipt.operation_type.value == "SIMULATION"
                and receipt.status is OperationStatus.CANCELLED
                and receipt.tool_execution_status == "CANCELLED"
                and status.receipt_fingerprint == receipt.fingerprint
            ):
                now = self._clock.now()
                return SimulationResult.create(
                    simulation_id=checked.simulation_id,
                    operation_id=operation.operation_id,
                    tenant_id=checked.tenant_id,
                    project_id=checked.project_id,
                    backend=checked.backend,
                    status=SimulationStatus.CANCELLED,
                    failure_class=SimulationFailureClass.CANCELLED,
                    reason_code="SIMULATION_CANCELLED",
                    started_at=now,
                    finished_at=now,
                    assertion_results=(),
                    artifact_references=(
                        f"artifact:{checked.firmware_artifact.artifact_id}",
                    ),
                    telemetry_references=(),
                    diagnostic_summary="simulation operation was cancelled",
                    request_fingerprint=checked.fingerprint,
                )
            return self._infrastructure_result(
                checked, operation.operation_id, "SIMULATION_RESULT_UNAVAILABLE"
            )
        try:
            result = validate_result(stored)
        except Exception:  # noqa: BLE001 - malformed result/receipt fails closed
            return self._infrastructure_result(
                checked, operation.operation_id, "SIMULATION_RESULT_BINDING_MISMATCH"
            )
        if (
            not self._receipt_binds_terminal_result(receipt, status, result)
            or result.simulation_id != checked.simulation_id
            or result.operation_id != operation.operation_id
            or result.request_fingerprint != checked.fingerprint
            or f"artifact:{checked.firmware_artifact.artifact_id}"
            not in result.artifact_references
            or not self._assertions_bind(checked, result)
        ):
            return self._infrastructure_result(
                checked, operation.operation_id, "SIMULATION_RESULT_BINDING_MISMATCH"
            )
        return result

    @staticmethod
    def _assertions_bind(request: SimulationRequest, result: SimulationResult) -> bool:
        expected = {item.assertion_id for item in request.assertions}
        observed = {item.assertion_id for item in result.assertion_results}
        return observed.issubset(expected) and (
            result.status is not SimulationStatus.PASS or observed == expected
        )

    @staticmethod
    def _receipt_binds_terminal_result(
        receipt: OperationReceipt | None,
        operation_status: OperationStatusView,
        result: SimulationResult,
    ) -> bool:
        """Require the persisted worker receipt to agree with the normalized result."""

        if receipt is None:
            return False
        if type(receipt) is not OperationReceipt:
            return False
        if (
            receipt.operation_id != result.operation_id
            or receipt.tenant_id != result.tenant_id
            or receipt.project_id != result.project_id
            or receipt.operation_type.value != "SIMULATION"
            or receipt.status is not operation_status.status
            or operation_status.receipt_fingerprint != receipt.fingerprint
            or result.operation_id is None
        ):
            return False
        expected = {
            SimulationStatus.PASS: (OperationStatus.SUCCEEDED, "SUCCEEDED"),
            SimulationStatus.FAIL: (OperationStatus.FAILED, "FAILED"),
            SimulationStatus.INFRASTRUCTURE_ERROR: (
                OperationStatus.FAILED,
                "UNAVAILABLE",
            ),
            SimulationStatus.CANCELLED: (OperationStatus.CANCELLED, "CANCELLED"),
        }
        if result.status is SimulationStatus.TIMEOUT:
            expected_status = (
                OperationStatus.TIMED_OUT
                if result.failure_class is SimulationFailureClass.INFRASTRUCTURE
                else OperationStatus.FAILED
            )
            expected_tool = (
                "TIMED_OUT"
                if result.failure_class is SimulationFailureClass.INFRASTRUCTURE
                else "FAILED"
            )
            expected = {SimulationStatus.TIMEOUT: (expected_status, expected_tool)}
        return expected.get(result.status) == (
            receipt.status,
            receipt.tool_execution_status,
        )

    def _non_terminal(
        self,
        request: SimulationRequest,
        operation_id: str,
        status: SimulationStatus,
        reason_code: str,
        *,
        started_at: datetime | None = None,
    ) -> SimulationResult:
        return SimulationResult.create(
            simulation_id=request.simulation_id,
            operation_id=operation_id,
            tenant_id=request.tenant_id,
            project_id=request.project_id,
            backend=request.backend,
            status=status,
            failure_class=SimulationFailureClass.NONE,
            reason_code=reason_code,
            started_at=started_at,
            finished_at=None,
            assertion_results=(),
            artifact_references=(f"artifact:{request.firmware_artifact.artifact_id}",),
            telemetry_references=(),
            diagnostic_summary="simulation operation has not reached a terminal state",
            request_fingerprint=request.fingerprint,
        )

    def _infrastructure_result(
        self, request: SimulationRequest, operation_id: str | None, reason_code: str
    ) -> SimulationResult:
        now = self._clock.now()
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
            diagnostic_summary="simulation capability is unavailable or failed closed",
            request_fingerprint=request.fingerprint,
        )


__all__ = ("SimulationService",)
