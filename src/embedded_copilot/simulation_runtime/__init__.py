"""Private v7.2.4 virtual-hardware runtime foundation.

The package is opt-in and is intentionally not exported from the product
package root or production composition.
"""

from .adapters import (
    FakeSimulationAdapter,
    QemuSimulationAdapter,
    RenodeSimulationAdapter,
    SimulationBackedTestAdapter,
    WokwiSimulationAdapter,
)
from .contracts import (
    PreparedSimulation,
    SimulationAssertion,
    SimulationAssertionKind,
    SimulationAssertionResult,
    SimulationBackend,
    SimulationBackendCapability,
    SimulationCapabilityStatus,
    SimulationExecutionBinding,
    SimulationExecutionMode,
    SimulationEvidence,
    SimulationFailureClass,
    SimulationRequest,
    SimulationResult,
    SimulationStatus,
)
from .memory import SimulationMemoryRecorder
from .operation import ExecutionOperationAdapter
from .service import SimulationService
from .stores import EphemeralPreparedSimulationStore, EphemeralSimulationResultStore
from .stores import JsonSimulationEvidenceStore, JsonSimulationResultStore
from .renode import RenodeGPIOConfig

__all__ = (
    "EphemeralPreparedSimulationStore",
    "EphemeralSimulationResultStore",
    "JsonSimulationEvidenceStore",
    "JsonSimulationResultStore",
    "ExecutionOperationAdapter",
    "FakeSimulationAdapter",
    "PreparedSimulation",
    "QemuSimulationAdapter",
    "RenodeSimulationAdapter",
    "SimulationAssertion",
    "SimulationAssertionKind",
    "SimulationAssertionResult",
    "SimulationBackedTestAdapter",
    "SimulationBackend",
    "SimulationBackendCapability",
    "SimulationCapabilityStatus",
    "SimulationExecutionBinding",
    "SimulationExecutionMode",
    "SimulationEvidence",
    "SimulationFailureClass",
    "SimulationMemoryRecorder",
    "SimulationRequest",
    "SimulationResult",
    "SimulationService",
    "SimulationStatus",
    "RenodeGPIOConfig",
    "WokwiSimulationAdapter",
)
