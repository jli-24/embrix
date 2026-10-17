from .analysis import DeterministicPCBAnalyzer as DeterministicPCBAnalyzer
from .differential_pair import *
from .exceptions import *
from .foundation import *
from .graph import DeterministicPCBGraphBuilder as DeterministicPCBGraphBuilder
from .mapping import *
from .models import *
from .ports import *
from .power_loop import *
from .return_path import *
from .service import EngineeringPCBService as EngineeringPCBService

# Decoupling verification consumes the shared verification-loop contracts.
# Keep that optional surface lazy so importing verification_loop itself does
# not recurse through engineering_consistency -> engineering_pcb -> decoupling.
_DECOUPLING_EXPORTS = frozenset(
    {
        "DecouplingEvaluationEngine",
        "DecouplingMeasurement",
        "DecouplingRelationshipResolver",
        "DecouplingVerifier",
        "PCBMeasurementQuality",
        "PCBPlacementCandidate",
        "PCBPreventEvaluation",
        "PCBPreventStatus",
        "DecouplingPreventEvaluator",
        "mark_decoupling_stale",
    }
)

_FINAL_CLOSURE_EXPORTS = frozenset(
    {
        "AcceptedRisk",
        "ApprovalDecision",
        "BlockingUnknownCategory",
        "CopperToEdgeEvidence",
        "DFTRequirement",
        "EngineeringApplicabilityDecision",
        "EngineeringCheck",
        "EngineeringEvidenceRequirement",
        "EngineeringEvidenceSource",
        "EngineeringVerificationType",
        "HumanApprovalBinding",
        "ManufacturerCapabilityRule",
        "ManufacturerProfile",
        "ManufacturerRuleSeverity",
        "PCBManufacturingMeasurements",
        "PCBEngineeringVerificationClosure",
        "PCBEngineeringVerificationService",
        "PCBEngineeringVerificationSummary",
        "ReadyForFabGate",
        "ReadyForFabStatus",
        "VerificationTransition",
        "build_jlcpcb_dfm_inputs",
        "build_jlcpcb_rigid_manufacturer_profile",
        "build_ready_for_fab_approval_target",
    }
)


def __getattr__(name: str):
    if name in _DECOUPLING_EXPORTS:
        from importlib import import_module

        module = import_module(".decoupling", __name__)
        value = getattr(module, name)
        globals()[name] = value
        return value
    if name in _FINAL_CLOSURE_EXPORTS:
        from importlib import import_module

        module = import_module(".final_closure", __name__)
        value = getattr(module, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
