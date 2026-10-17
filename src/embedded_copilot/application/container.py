from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass, field

from embedded_copilot.engineering_context import EngineeringContextProviderPort
from embedded_copilot.engineering_research import EngineeringResearchService
from embedded_copilot.model_gateway import (
    ModelGatewayPort,
    ModelInvocationGenerationOptions,
    ModelPolicy,
)

from .exceptions import ApplicationDependencyError
from .ports import (
    ApplicationAgentRuntimePort,
    ApplicationAuditSinkPort,
    ApplicationClockPort,
    ApplicationDebugAgentPort,
    ApplicationDebugPromptPort,
    ApplicationFirmwareAgentPort,
    ApplicationFirmwarePromptPort,
    ModelInvocationPort,
)


@dataclass(frozen=True, slots=True)
class ApplicationDependencyContainer:
    agent_runtime: ApplicationAgentRuntimePort
    context_provider: EngineeringContextProviderPort
    audit_sink: ApplicationAuditSinkPort
    clock: ApplicationClockPort
    model_gateway: ModelGatewayPort | None = None
    model_policy: ModelPolicy | None = None
    mode: str = "LEGACY"
    generation_options: ModelInvocationGenerationOptions = field(default_factory=ModelInvocationGenerationOptions)
    model_invocation_service: ModelInvocationPort | None = None
    firmware_agent: ApplicationFirmwareAgentPort | None = None
    firmware_prompt: ApplicationFirmwarePromptPort | None = None
    debug_agent: ApplicationDebugAgentPort | None = None
    debug_prompt: ApplicationDebugPromptPort | None = None
    research_service: EngineeringResearchService | None = None
    engineering_object_integration_factory: Callable[[str], object] | None = None
    firmware_build_execution_bridge: object | None = None

    def __post_init__(self) -> None:
        if self.mode not in {"GOVERNED", "LEGACY"}:
            raise ApplicationDependencyError()
        if self.mode == "GOVERNED":
            if not isinstance(self.model_invocation_service, ModelInvocationPort):
                raise ApplicationDependencyError()
            object.__setattr__(self, "model_gateway", None)
            object.__setattr__(self, "model_policy", None)
        elif self.model_invocation_service is not None:
            raise ApplicationDependencyError()
        if (
            not isinstance(self.agent_runtime, ApplicationAgentRuntimePort)
            or not isinstance(self.context_provider, EngineeringContextProviderPort)
            or (self.mode == "LEGACY" and not isinstance(self.model_gateway, ModelGatewayPort))
            or not isinstance(self.audit_sink, ApplicationAuditSinkPort)
            or not isinstance(self.clock, ApplicationClockPort)
            or (
                self.model_invocation_service is not None
                and not isinstance(self.model_invocation_service, ModelInvocationPort)
            )
            or (
                self.firmware_agent is not None
                and not isinstance(self.firmware_agent, ApplicationFirmwareAgentPort)
            )
            or (
            self.firmware_prompt is not None
                and not isinstance(self.firmware_prompt, ApplicationFirmwarePromptPort)
            )
            or (
                self.debug_agent is not None
                and not isinstance(self.debug_agent, ApplicationDebugAgentPort)
            )
            or (
                self.debug_prompt is not None
                and not isinstance(self.debug_prompt, ApplicationDebugPromptPort)
            )
            or (
                self.research_service is not None
                and not isinstance(self.research_service, EngineeringResearchService)
            )
            or (
                self.engineering_object_integration_factory is not None
                and not callable(self.engineering_object_integration_factory)
            )
            or (
                self.firmware_build_execution_bridge is not None
                and not callable(
                    getattr(self.firmware_build_execution_bridge, "create_operation", None)
                )
            )
        ):
            raise ApplicationDependencyError()
        if self.mode == "GOVERNED":
            if not isinstance(self.generation_options, ModelInvocationGenerationOptions):
                raise ApplicationDependencyError()
            object.__setattr__(
                self,
                "generation_options",
                ModelInvocationGenerationOptions.model_validate(
                    copy.deepcopy(self.generation_options.model_dump(mode="python"))
                ),
            )
            return
        try:
            checked = ModelPolicy.model_validate(
                copy.deepcopy(self.model_policy.model_dump(mode="python"))
            )
        except Exception as error:
            raise ApplicationDependencyError() from error
        object.__setattr__(self, "model_policy", checked)


__all__ = ("ApplicationDependencyContainer",)
