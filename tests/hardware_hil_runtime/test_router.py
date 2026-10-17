from embedded_copilot.agent_runtime import (
    AgentCapabilityRequest,
    CapabilityType,
    HardwareHILTestInput,
    HardwareHILTestMode,
    classify_request,
)
from embedded_copilot.application import ApplicationQuestionRequest

from .test_contracts import NOW, proposal_request


def test_hil_marker_routes_before_general_hardware_observation():
    request = AgentCapabilityRequest(
        application_request=ApplicationQuestionRequest(
            request_id="request-1",
            trace_id="trace-1",
            project_id="project-1",
            requester="engineer-1",
            question="run a hardware-in-the-loop fixture test",
            constraints=(),
            requested_at=NOW,
        ),
        payload=HardwareHILTestInput(
            mode=HardwareHILTestMode.PROPOSE,
            proposal_request=proposal_request(),
        ),
    )
    assert (
        classify_request(request).selected_capability
        is CapabilityType.HARDWARE_HIL_TEST
    )
