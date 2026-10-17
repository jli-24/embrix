import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { ContextReference, RuntimePanel as RuntimePanelView } from "../api/chatClient";
import type { ArchitectureDesign, CapabilityEvaluation, ConversationTurn, DesignTruth, EngineeringKnowledgeModel, EngineeringPlan, EngineeringWorkflow, ExecutionAuthorization, FirmwareSourceGenerationState, FirmwareSpecification, HardwareDesignSpecification, ImplementationSpecification, ProjectContext, ProjectView, RequirementContext, SimulationSpecification, TeachingContext, VerificationSpecification } from "../api/projectClient";
import { ChatPanel } from "../chat/ChatPanel";
import { AssistantMessage } from "../chat/AssistantMessage";
import { DesktopShell } from "../layout/DesktopShell";
import { ProjectCreatePanel } from "../project/ProjectCreatePanel";
import { RuntimePanel } from "../runtime/RuntimePanel";
import { EngineeringContextPanel, EngineeringRoadmap, FirmwareSourceProposalCard } from "../layout/AgentSurfacePanels";
import { toProjectWorkspaceIdentity } from "../projectWorkspaceIdentity";
import { projectContextWorkspaceFixture } from "../acceptanceFixtures/projectContextFixture";

const fingerprint = `sha256:${"a".repeat(64)}`;
const evidence: ContextReference = { tenant_id: "tenant", project_id: "gateway", reference_id: "obs-1", reference_kind: "EVIDENCE", source_fingerprint: fingerprint, display_label: "HIL Observation", fingerprint };
const turn: ConversationTurn = { turn_id: "turn-1", request_id: "request-1", status: "COMPLETED", question: "为什么 I2C 读取失败？", answer_text: "根据当前工程上下文，I2C 时序需要复核。", evidence: [{ source_type: "HIL", label: "I2C timeout observation", reference_id: "obs-1", status: "Observed", summary: "I2C timeout observed", source_fingerprint: fingerprint, observed_at: "2026-08-10T14:30:00Z" }], recommendations: [{ review: "降低 I2C 频率", reason: null, status: "Proposal", reference_id: "proposal-1", source_fingerprint: fingerprint }], context_references: [], error_code: null, created_at: "2026-08-10T14:30:00Z", completed_at: "2026-08-10T14:30:01Z" };
const project: ProjectView = { project_id: "gateway", tenant_id: "tenant", name: "Gateway", location_id: "default-project-root", directory_name: "gateway", creation_mode: "CREATE_NEW", relative_path: "gateway", project_type: "ESP_IDF", initialization_status: "READY", platform: "ESP32-S3", version: null, branch: "main", failure_code: null, created_at: "2026-08-10T14:30:00Z", updated_at: "2026-08-10T14:30:00Z", last_activity_at: "2026-08-10T14:30:00Z", fingerprint };
const context: ProjectContext = { project_id: "gateway", status: "READY", groups: [{ category: "Firmware", references: [{ reference_id: "file-1", category: "Firmware", display_label: "sensor_i2c.c", source_fingerprint: fingerprint, fingerprint }] }], evidence_sources: [{ source: "HIL", count: 1 }], capabilities: [{ capability: "Project Files", status: "AVAILABLE" }], generated_at: "2026-08-10T14:30:00Z", fingerprint };

const chatProps = { sessionReady: true, turns: [turn], pendingQuestion: null, availableReferences: [evidence], pending: false, error: null, selected: [], evidenceDisplay: "expanded" as const, onToggleReference: () => undefined, onAttach: () => undefined, onSubmit: () => undefined, onRetry: () => undefined };
const requirement: RequirementContext = {
  project_id: "gateway",
  conversation_id: "conversation-1",
  standard_fields: [],
  requirements: [
    { requirement_id: "requirement-goal", category: "PRODUCT_GOAL", normalized_value: "STM32 智能小车", status: "CONFIRMED", provenance: "USER_UTTERANCE" },
    { requirement_id: "requirement-platform", category: "TARGET_PLATFORM", normalized_value: "STM32", status: "CONFIRMED", provenance: "USER_UTTERANCE" },
    { requirement_id: "requirement-functional", category: "FUNCTIONAL", normalized_value: "LINE_FOLLOWING", status: "CONFIRMED", provenance: "USER_UTTERANCE" },
  ],
  constraints: [],
  proposed_items: [{ requirement_id: "requirement-communication", category: "COMMUNICATION", normalized_value: "BLUETOOTH", status: "PROPOSED", provenance: "PROVIDER_RESPONSE" }],
  open_questions: [{ category: "POWER_SOURCE", question: "请确认供电方式。", provenance: "SYSTEM" }],
  acceptance_criteria: [{ given: "存在清晰轨迹", when: "小车启动", then: "小车持续沿轨迹行驶", verification: "轨迹测试", status: "PROPOSED" }],
  conflicts: [{ description: "功耗与续航需要权衡。", severity: "NON_BLOCKING", status: "OPEN" }],
  status: "CLARIFICATION_REQUIRED",
  lifecycle: "REQUIREMENTS_REFINING",
  fingerprint: fingerprint,
};
const plan: EngineeringPlan = {
  plan_id: "plan-1",
  project_id: "gateway",
  requirement_id: "requirement-1",
  title: "STM32 Smart Car Development Plan",
  objective: "Build embedded robot platform",
  status: "PROPOSED",
  phases: [
    { phase_id: "phase-1", name: "Requirement Confirmation", description: "confirmed", status: "COMPLETED", tasks: [], outputs: [], verification: [] },
    { phase_id: "phase-2", name: "System Architecture", description: "architecture", status: "PENDING", tasks: [], outputs: [], verification: [] },
    { phase_id: "phase-3", name: "Hardware Design", description: "hardware", status: "PENDING", tasks: [], outputs: [], verification: [] },
    { phase_id: "phase-4", name: "Firmware Development", description: "firmware", status: "PENDING", tasks: [], outputs: [], verification: [] },
  ],
  verification_strategy: [],
  fingerprint,
};
const architecture: ArchitectureDesign = {
  architecture_id: "architecture-1",
  project_id: "gateway",
  plan_id: "plan-1",
  title: "STM32 Smart Car Architecture",
  objective: "Separate confirmed hardware and software responsibilities.",
  status: "PROPOSED",
  system_components: [
    { component_id: "controller", name: "STM32", category: "CONTROLLER", responsibility: "control", level: "HARDWARE", confidence: "EXPLICIT" },
    { component_id: "control", name: "Control", category: "CONTROL", responsibility: "logic", level: "SOFTWARE", confidence: "INFERRED" },
  ],
  interfaces: [{ interface_id: "interface-1", source_component_id: "controller", target_component_id: "control", interface_type: "SOFTWARE", protocol: "UNKNOWN", verification: "review", confidence: "UNKNOWN" }],
  boundary: { hardware_component_ids: ["controller"], software_component_ids: ["control"] },
  data_flow: [{ flow_id: "flow-1", flow_type: "DATA", source_component_id: "controller", target_component_id: "control", data_or_command: "input", confidence: "INFERRED" }],
  control_flow: [],
  decisions: [],
  verification: [],
  constraints: [],
  context_fingerprint: fingerprint,
  plan_fingerprint: fingerprint,
  fingerprint,
};
const designTruth: DesignTruth = {
  truth_id: "truth-1",
  project_id: "gateway",
  design_revision: "v1",
  architecture_id: "architecture-1",
  components: [
    { component_id: "design-controller", name: "STM32", type: "HARDWARE", category: "MCU", description: "controller", confidence: 1, truth_level: "EXPLICIT", evidence_refs: ["evidence-1"] },
    { component_id: "design-control", name: "Control", type: "SOFTWARE", category: "CONTROL", description: "logic", confidence: 0.5, truth_level: "INFERRED", evidence_refs: ["evidence-2"] },
  ],
  signals: [],
  pins: [],
  peripherals: [],
  nets: [],
  interfaces: [{ interface_id: "design-interface-1", source: "design-controller", target: "design-control", protocol: "UNKNOWN", direction: "SOURCE_TO_TARGET", confidence: 0, truth_level: "UNKNOWN", evidence_refs: ["evidence-3"] }],
  power_domains: [],
  firmware_bindings: [],
  evidence_bindings: [
    { binding_id: "evidence-1", entity_id: "design-controller", project_id: "gateway", source: "ARCHITECTURE", source_id: "architecture-1", artifact_ref: "architecture-1", locator: "component:controller", source_fingerprint: fingerprint, relationship: "projection", truth_level: "EXPLICIT" },
    { binding_id: "evidence-2", entity_id: "design-control", project_id: "gateway", source: "ARCHITECTURE", source_id: "control", artifact_ref: "architecture-1", locator: "component:control", source_fingerprint: fingerprint, relationship: "projection", truth_level: "INFERRED" },
    { binding_id: "evidence-3", entity_id: "design-interface-1", project_id: "gateway", source: "ARCHITECTURE", source_id: "interface-1", artifact_ref: "architecture-1", locator: "interface:interface-1", source_fingerprint: fingerprint, relationship: "projection", truth_level: "UNKNOWN" },
  ],
  provenance: [],
  fingerprint,
};
const hardwareDesignSpec: HardwareDesignSpecification = {
  hardware_spec_id: "hardware-spec-internal",
  project_id: "gateway",
  architecture_id: "architecture-1",
  workflow_id: "workflow-1",
  architecture_fingerprint: fingerprint,
  workflow_fingerprint: fingerprint,
  design_revision: "revision-internal",
  title: "Hardware planning",
  system_requirements: ["Support dual motor drive"],
  hardware_domains: [
    { domain_id: "domain-controller", hardware_spec_id: "hardware-spec-internal", name: "Controller", type: "CONTROLLER", requirements: ["Control system behavior"], interfaces: ["interface-gpio"], verification_refs: [], fingerprint },
    { domain_id: "domain-power", hardware_spec_id: "hardware-spec-internal", name: "Power", type: "POWER", requirements: ["Battery powered"], interfaces: [], verification_refs: [], fingerprint },
    { domain_id: "domain-sensor", hardware_spec_id: "hardware-spec-internal", name: "Sensor", type: "SENSOR", requirements: ["Support obstacle detection"], interfaces: [], verification_refs: [], fingerprint },
  ],
  component_candidates: [],
  interfaces: [{ interface_id: "interface-gpio", source: "domain-controller", target: "domain-sensor", interface_type: "GPIO", signal_refs: [], direction: "UNKNOWN", constraints: [], evidence_refs: [], fingerprint }],
  decisions: [{ decision_id: "decision-power", subject: "Power architecture", options: ["Battery + Buck"], selected_option: null, rationale: "Review required", status: "OPEN", evidence_refs: [], fingerprint }],
  constraints: [],
  verification_requirements: [],
  source_refs: [],
  provenance: [],
  schematic_plan: null,
  pcb_plan: null,
  fingerprint,
};
const firmwareSpec: FirmwareSpecification = {
  firmware_spec_id: "firmware-spec-internal",
  project_id: "gateway",
  conversation_id: "conversation-1",
  hardware_design_id: "hardware-spec-internal",
  architecture_id: "architecture-1",
  workflow_id: "workflow-1",
  hardware_design_fingerprint: fingerprint,
  workflow_fingerprint: fingerprint,
  title: "Firmware planning",
  requirements: ["Support obstacle detection"],
  runtime_constraints: [],
  software_interfaces: ["interface-gpio"],
  modules: [
    { module_id: "firmware-module-application", firmware_spec_id: "firmware-spec-internal", name: "Application", module_type: "APPLICATION", responsibility: "Coordinates application behavior", dependencies: [], hardware_bindings: ["firmware-binding-sensor"], verification_refs: ["firmware-verification-application"], source_refs: [], fingerprint },
    { module_id: "firmware-module-sensor", firmware_spec_id: "firmware-spec-internal", name: "Sensor Acquisition", module_type: "DRIVER", responsibility: "Acquire sensor input", dependencies: [], hardware_bindings: ["firmware-binding-sensor"], verification_refs: ["firmware-verification-sensor"], source_refs: [], fingerprint },
    { module_id: "firmware-module-motor", firmware_spec_id: "firmware-spec-internal", name: "Motor Control", module_type: "CONTROL", responsibility: "Control actuator behavior", dependencies: [], hardware_bindings: [], verification_refs: [], source_refs: [], fingerprint },
  ],
  bindings: [{ binding_id: "firmware-binding-sensor", hardware_interface_id: "interface-gpio", firmware_module_id: "firmware-module-sensor", interface_role: "UNKNOWN", direction: "UNKNOWN", signal_refs: [], evidence_refs: [], fingerprint }],
  tasks: [
    { task_id: "firmware-task-application", firmware_spec_id: "firmware-spec-internal", module_id: "firmware-module-application", title: "Design Application module", task_type: "DESIGN", dependencies: [], expected_outputs: ["Application"], verification_refs: ["firmware-verification-application"], status: "PLANNED", fingerprint },
    { task_id: "firmware-task-sensor", firmware_spec_id: "firmware-spec-internal", module_id: "firmware-module-sensor", title: "Design Sensor Acquisition module", task_type: "DESIGN", dependencies: [], expected_outputs: ["Sensor Acquisition"], verification_refs: ["firmware-verification-sensor"], status: "PLANNED", fingerprint },
    { task_id: "firmware-task-motor", firmware_spec_id: "firmware-spec-internal", module_id: "firmware-module-motor", title: "Design Motor Control module", task_type: "DESIGN", dependencies: [], expected_outputs: ["Motor Control"], verification_refs: [], status: "PLANNED", fingerprint },
  ],
  verification_requirements: [
    { verification_id: "firmware-verification-application", target_module: "firmware-module-application", method: "Review and software test", acceptance_refs: [], evidence_requirements: [], simulation_refs: [], fingerprint },
    { verification_id: "firmware-verification-sensor", target_module: "firmware-module-sensor", method: "Sensor acquisition test", acceptance_refs: [], evidence_requirements: [], simulation_refs: [], fingerprint },
  ],
  source_refs: [],
  provenance: [],
  design_revision: "revision-internal",
  fingerprint,
};
const simulationSpec: SimulationSpecification = {
  simulation_spec_id: "simulation-spec-internal",
  project_id: "gateway",
  conversation_id: "conversation-1",
  hardware_design_id: "hardware-spec-internal",
  firmware_spec_id: "firmware-spec-internal",
  workflow_id: "workflow-1",
  hardware_design_fingerprint: fingerprint,
  firmware_spec_fingerprint: fingerprint,
  workflow_fingerprint: fingerprint,
  title: "Simulation planning",
  design_intent: "INTERFACE_VALIDATION",
  status: "PLANNED",
  targets: ["simulation-model-controller", "simulation-model-firmware"],
  models: [
    { model_id: "simulation-model-controller", simulation_spec_id: "simulation-spec-internal", name: "Controller", model_type: "CONTROLLER", source_reference: "domain-controller", parameters: [], interfaces: ["interface-gpio"], evidence_refs: [], fingerprint },
    { model_id: "simulation-model-firmware", simulation_spec_id: "simulation-spec-internal", name: "Sensor Acquisition", model_type: "FIRMWARE_MODULE", source_reference: "firmware-module-sensor", parameters: [], interfaces: ["firmware-binding-sensor"], evidence_refs: [], fingerprint },
  ],
  scenarios: [{ scenario_id: "simulation-scenario-interface", simulation_spec_id: "simulation-spec-internal", title: "Controller to Sensor Interface Test", scenario_type: "INTERFACE_TEST", inputs: [], expected_outputs: ["Sensor acquisition test"], related_models: ["simulation-model-controller", "simulation-model-firmware"], acceptance_refs: [], fingerprint }],
  evidence_requirements: [{ evidence_id: "simulation-evidence-interface", simulation_spec_id: "simulation-spec-internal", scenario_id: "simulation-scenario-interface", evidence_type: "UNKNOWN", description: "Pending evidence for interface test", acceptance_refs: [], fingerprint }],
  verification_refs: [],
  source_refs: [],
  provenance: [],
  design_revision: "revision-internal",
  fingerprint,
};
const verificationSpec: VerificationSpecification = {
  schema_version: "1.0",
  verification_id: "verification-spec-internal",
  project_id: "gateway",
  conversation_id: "conversation-1",
  hardware_design_id: "hardware-spec-internal",
  firmware_spec_id: "firmware-spec-internal",
  simulation_spec_id: "simulation-spec-internal",
  workflow_id: "workflow-1",
  hardware_design_fingerprint: fingerprint,
  firmware_spec_fingerprint: fingerprint,
  simulation_spec_fingerprint: fingerprint,
  workflow_fingerprint: fingerprint,
  design_revision: "revision-internal",
  verification_intent: "CONTROL_VALIDATION",
  cases: [{ case_id: "verification-case-control", verification_id: "verification-spec-internal", title: "Motor Control Verification", case_type: "CONTROL", verification_intent: "CONTROL_VALIDATION", target: "firmware-module-motor", related_requirement_refs: [], related_design_refs: [], method: "Review", expected_behavior: ["Control actuator behavior"], evidence_refs: [], status: "PLANNED", fingerprint }],
  evidence_requirements: [{ evidence_id: "verification-evidence-waveform", verification_id: "verification-spec-internal", case_id: "verification-case-control", evidence_type: "WAVEFORM", description: "Waveform evidence required", source_reference: "simulation-evidence-control", acceptance_refs: [], fingerprint }],
  acceptance_mapping: [],
  status: "PLANNED",
  provenance: [],
  fingerprint,
};
const implementationSpec: ImplementationSpecification = {
  implementation_id: "implementation-spec-internal",
  project_id: "gateway",
  conversation_id: "conversation-1",
  hardware_design_id: "hardware-spec-internal",
  firmware_spec_id: "firmware-spec-internal",
  simulation_spec_id: "simulation-spec-internal",
  verification_id: "verification-spec-internal",
  workflow_id: "workflow-1",
  hardware_design_fingerprint: fingerprint,
  firmware_spec_fingerprint: fingerprint,
  simulation_spec_fingerprint: fingerprint,
  verification_fingerprint: fingerprint,
  workflow_fingerprint: fingerprint,
  design_revision: "revision-internal",
  tasks: [
    { task_id: "implementation-task-motor", implementation_id: "implementation-spec-internal", title: "Implement Motor Control", task_type: "FIRMWARE_MODULE", implementation_intent: "CONTROL_IMPLEMENTATION", description: "Control actuator behavior", source_refs: [], dependencies: [], expected_artifacts: ["expected-artifact-motor"], verification_refs: ["verification-case-control"], acceptance_refs: [], status: "PLANNED", fingerprint },
    { task_id: "implementation-task-interface", implementation_id: "implementation-spec-internal", title: "Implement interface-1 Interface", task_type: "INTERFACE_IMPLEMENTATION", implementation_intent: "INTERFACE_ADAPTATION", description: "Declared hardware interface", source_refs: [], dependencies: [], expected_artifacts: ["expected-artifact-interface"], verification_refs: [], acceptance_refs: [], status: "PLANNED", fingerprint },
  ],
  expected_artifacts: [
    { artifact_id: "expected-artifact-motor", implementation_id: "implementation-spec-internal", artifact_type: "SOURCE_CODE", name: "Motor Control Firmware Module", description: "Control actuator behavior", source_refs: [], verification_refs: ["verification-case-control"], fingerprint },
    { artifact_id: "expected-artifact-interface", implementation_id: "implementation-spec-internal", artifact_type: "CONFIGURATION", name: "interface-1 Interface Configuration", description: "Declared hardware interface", source_refs: [], verification_refs: [], fingerprint },
  ],
  dependencies: [],
  verification_refs: ["verification-case-control"],
  status: "PLANNED",
  provenance: [],
  fingerprint,
};
const capabilityEvaluation: CapabilityEvaluation = {
  evaluation_id: "capability-evaluation-internal",
  project_id: "gateway",
  implementation_id: "implementation-spec-internal",
  implementation_fingerprint: fingerprint,
  capabilities: [
    { capability_id: "FIRMWARE_CODE_GENERATION", name: "Firmware Code Generation", category: "FIRMWARE", description: "Planned source generation", risk_level: "HIGH", requires_approval: true, status: "PLANNED", source_refs: ["implementation-task-motor"], fingerprint },
    { capability_id: "ARTIFACT_CREATION", name: "Artifact Creation", category: "ARTIFACT", description: "Planned artifact creation", risk_level: "LOW", requires_approval: false, status: "PLANNED", source_refs: ["implementation-task-interface"], fingerprint },
  ],
  bindings: [],
  approval_requirements: [{ approval_id: "approval-internal", intent_id: "intent-internal", required_capabilities: ["FIRMWARE_CODE_GENERATION"], risk_level: "HIGH", approval_status: "PENDING", fingerprint }],
  fingerprint,
};
const executionAuthorization: ExecutionAuthorization = {
  authorization_id: "execution-authorization-internal",
  project_id: "gateway",
  conversation_id: "conversation-1",
  capability_evaluation_id: "capability-evaluation-internal",
  requests: [
    { request_id: "request-firmware-internal", intent_id: "intent-firmware-internal", capability_id: "FIRMWARE_CODE_GENERATION", project_id: "gateway", conversation_id: "conversation-1", requested_by: "engineer-1", request_reason: "Authorization requested", risk_level: "HIGH", status: "REQUESTED", source_refs: ["capability-evaluation-internal"], fingerprint },
    { request_id: "request-artifact-internal", intent_id: "intent-artifact-internal", capability_id: "ARTIFACT_CREATION", project_id: "gateway", conversation_id: "conversation-1", requested_by: "engineer-1", request_reason: "Authorization requested", risk_level: "LOW", status: "REQUESTED", source_refs: ["capability-evaluation-internal"], fingerprint },
    { request_id: "request-flash-internal", intent_id: "intent-flash-internal", capability_id: "FLASH_DEVICE", project_id: "gateway", conversation_id: "conversation-1", requested_by: "engineer-1", request_reason: "Authorization requested", risk_level: "CRITICAL", status: "REQUESTED", source_refs: ["capability-evaluation-internal"], fingerprint },
  ],
  policies: [
    { policy_id: "policy-firmware-internal", capability_id: "FIRMWARE_CODE_GENERATION", risk_level: "HIGH", requires_user_confirmation: true, requires_review: false, allowed: true, fingerprint },
    { policy_id: "policy-artifact-internal", capability_id: "ARTIFACT_CREATION", risk_level: "LOW", requires_user_confirmation: false, requires_review: false, allowed: true, fingerprint },
    { policy_id: "policy-flash-internal", capability_id: "FLASH_DEVICE", risk_level: "CRITICAL", requires_user_confirmation: true, requires_review: true, allowed: true, fingerprint },
  ],
  decisions: [
    { decision_id: "decision-firmware-internal", request_id: "request-firmware-internal", decision: "PENDING", reason: "Awaiting authorization", approved_by: null, timestamp_reference: null, fingerprint },
    { decision_id: "decision-artifact-internal", request_id: "request-artifact-internal", decision: "PENDING", reason: "Awaiting authorization", approved_by: null, timestamp_reference: null, fingerprint },
    { decision_id: "decision-flash-internal", request_id: "request-flash-internal", decision: "PENDING", reason: "Awaiting authorization", approved_by: null, timestamp_reference: null, fingerprint },
  ],
  status: "PENDING",
  fingerprint,
};
const firmwareSourceGeneration: FirmwareSourceGenerationState = {
  request: {
    request_id: "source-generation-request-internal",
    project_id: "gateway",
    conversation_id: "conversation-1",
    requester: "engineer-1",
    implementation_task_id: "implementation-task-motor",
    firmware_module_id: "firmware-module-motor",
    authorization_request_id: "request-firmware-internal",
    verification_refs: ["verification-case-control"],
    fingerprint,
  },
  proposal: {
    proposal_id: "source-proposal-internal",
    firmware_module_id: "firmware-module-motor",
    module_name: "Motor Control",
    files: [
      { relative_path: "generated/motor_control.h", content_fingerprint: fingerprint, size_bytes: 120 },
      { relative_path: "generated/motor_control.c", content_fingerprint: fingerprint, size_bytes: 180 },
    ],
    verification_refs: ["verification-case-control"],
    status: "AWAITING_APPROVAL",
    firmware_evidence_parse: "UNAVAILABLE",
    fingerprint,
  },
  decision: { decision: "PENDING" },
  operation_id: null,
  status: "AWAITING_APPROVAL",
  fingerprint,
};
const knowledgeModel: EngineeringKnowledgeModel = {
  schema_version: "1.0",
  knowledge_id: "knowledge-internal",
  project_id: "gateway",
  conversation_id: "conversation-1",
  source_truth_fingerprint: fingerprint,
  title: "工程知识模型",
  summary: "只读结构化工程知识",
  system_context: [],
  subsystems: ["entity-subsystem-internal"],
  entities: [{ entity_id: "entity-subsystem-internal", knowledge_id: "knowledge-internal", entity_type: "SUBSYSTEM", name: "Motor Control", description: "motor control subsystem", responsibility: "control motors", truth_level: "EXPLICIT", confidence: "HIGH", source_truth_refs: ["design-component-1"], evidence_bindings: ["binding-internal"], fingerprint }],
  relationships: [],
  learning_topics: [{ topic_id: "topic-internal", title: "I2C Communication", description: "Understand the confirmed interface.", related_entities: ["entity-subsystem-internal"], difficulty: "FOUNDATION", source_refs: ["interface-i2c"], evidence_bindings: ["binding-internal"], fingerprint }],
  modification_points: [],
  design_decisions: [],
  risks: [],
  evidence_bindings: ["binding-internal"],
  provenance: [{ plan_id: "plan-internal" }],
  fingerprint,
};

const teachingContext: TeachingContext = {
  schema_version: "1.0",
  teaching_context_id: "teaching-context-internal",
  project_id: "gateway",
  conversation_id: "conversation-1",
  knowledge_id: "knowledge-internal",
  subject: {
    subject_id: "teaching-subject-internal",
    entity_id: "entity-subsystem-internal",
    entity_type: "SUBSYSTEM",
    title: "Motor Control",
    summary: "Confirmed motor control subsystem",
    related_entities: [],
    source_refs: ["design-component-1"],
    evidence_bindings: ["binding-internal"],
    confidence: "HIGH",
    fingerprint,
  },
  scope: "ENGINEERING",
  difficulty: "INTERMEDIATE",
  learning_mode: "ENGINEERING",
  related_entities: [],
  learning_path: {
    path_id: "teaching-path-internal",
    teaching_context_id: "teaching-context-internal",
    steps: [{ step_id: "teaching-step-internal", step_type: "COMPONENT", title: "Component", step_summary: "确认组件实体及其已绑定证据", related_entities: ["entity-subsystem-internal"], order: 1, evidence_refs: ["binding-internal"], fingerprint }],
    fingerprint,
  },
  evidence_bindings: ["binding-internal"],
  source_knowledge_schema_version: "1.0",
  source_knowledge_fingerprint: fingerprint,
  fingerprint,
};

const engineeringWorkflow: EngineeringWorkflow = {
  workflow_id: "workflow-internal",
  project_id: "gateway",
  conversation_id: "conversation-1",
  plan_id: "plan-1",
  architecture_id: "architecture-1",
  title: "STM32 Smart Car Architecture",
  status: "PLANNED",
  phases: [
    { phase_id: "workflow-phase-1", workflow_id: "workflow-internal", phase_type: "SYSTEM_DESIGN", name: "System Architecture", description: "architecture", order: 1, status: "COMPLETED", tasks: [{ task_id: "workflow-task-1", phase_id: "workflow-phase-1", title: "Define system boundary", description: "architecture", task_type: "DESIGN", status: "COMPLETED", dependencies: [], input_refs: [], output_refs: [], verification_refs: [], expected_outputs: [], verification_requirements: [] }], outputs: [], verification_refs: [], dependencies: [] },
    { phase_id: "workflow-phase-2", workflow_id: "workflow-internal", phase_type: "HARDWARE_DESIGN", name: "Hardware Design", description: "hardware", order: 2, status: "PENDING", tasks: [{ task_id: "workflow-task-2", phase_id: "workflow-phase-2", title: "Design power circuit", description: "hardware", task_type: "DESIGN", status: "PENDING", dependencies: [], input_refs: [], output_refs: ["artifact-internal"], verification_refs: [], expected_outputs: ["artifact-internal"], verification_requirements: [] }], outputs: [{ artifact_id: "artifact-internal", task_id: "workflow-task-2", artifact_type: "DOCUMENT", name: "Hardware Design Proposal", status: "PENDING", source_refs: ["plan-output-1"] }], verification_refs: [], dependencies: ["workflow-phase-1"] },
  ],
  current_phase_id: "workflow-phase-2",
  workflow_version: "1.0",
  provenance: [],
  fingerprint,
};

describe("copilot desktop components", () => {
  it("renders four independent IDE regions", () => {
    const markup = renderToStaticMarkup(<DesktopShell density="compact" theme="dark" top={<div>TopBar</div>} left={<div>Projects</div>} center={<div>Copilot</div>} right={<div>Context</div>} leftOpen rightOpen onCloseLeft={() => undefined} onCloseRight={() => undefined} />);
    expect(markup).toContain("ide-shell--theme-dark");
    for (const region of ["ide-shell__top", "ide-shell__left", "ide-shell__center", "ide-shell__right"]) expect(markup).toContain(region);
  });

  it("renders a natural Embrix response without report projection sections", () => {
    const markup = renderToStaticMarkup(<ChatPanel title="I2C 分析" projectName="ESP32-S3 Sensor Gateway" updatedAt="2026-08-10T14:14:00Z" {...chatProps} />);
    for (const label of ["Embrix", "I2C 时序需要复核"]) expect(markup).toContain(label);
    expect(markup).not.toContain("复制全部");
    expect(markup).not.toContain("复制代码");
    for (const label of ["证据", "I2C timeout observation", "建议", "降低 I2C 频率", "Proposal"]) expect(markup).not.toContain(label);
    expect(markup).not.toContain("Copilot 分析");
    expect(markup).not.toContain("ESP32-S3 Sensor Gateway");
    expect(markup).not.toContain("avatar");
    expect(markup).not.toContain("robot");
  });

  it("keeps the default response surface conversational", () => {
    const markup = renderToStaticMarkup(<AssistantMessage answer={"STM32 智能小车\n\nCONFIRMED\n\nRequirement ID: REQ-001\n\n先确认底盘和通信需求。\n\nUNKNOWN"} evidence={[]} recommendations={[]} pending={false} failed={false} evidenceDisplay="compact" />);
    expect(markup).toContain("先确认底盘和通信需求。");
    expect(markup).not.toContain("CONFIRMED");
    expect(markup).not.toContain("UNKNOWN");
    expect(markup).not.toContain("Requirement ID");
  });

  it("renders only real qualitative engineering resources", () => {
    const panel: RuntimePanelView = { hil_status: "OBSERVED", eda_status: "UNAVAILABLE", memory_statuses: [], runtime_health: [] };
    const markup = renderToStaticMarkup(<RuntimePanel project={project} context={context} panel={panel} serviceError={null} />);
    for (const label of ["工程上下文", "当前项目", "当前引用", "证据来源", "工程资源", "ESP32-S3", "1 references", "Project Files"]) expect(markup).toContain(label);
    expect(markup).not.toContain("sensor_i2c.c");
    for (const invented of ["120mA", "100%", "98%", "12 items", "Workspace", "Runtime Dashboard"]) expect(markup).not.toContain(invented);
  });

  it("projects Context, Status and Activity without exposing internal state", () => {
    const fixture = projectContextWorkspaceFixture;
    const markup = renderToStaticMarkup(<EngineeringContextPanel identity={toProjectWorkspaceIdentity(fixture.project, null)} project={fixture.project} context={fixture.context} projection={{ projectId: fixture.project?.project_id ?? "", references: [], evidenceSources: fixture.context?.evidence_sources ?? [], resources: [] }} memoryProjection={null} resources={[]} workflow={null} hardwareDesignSpec={hardwareDesignSpec} />);
    for (const label of ["Engineering Context", "Project", "HARDWARE DESIGN", "Hardware Domains", "Controller", "Power", "Sensor", "Interfaces", "GPIO", "Design Decisions", "Pending", "Hardware", "Firmware", "Documents", "Evidence", "Hardware reference 1", "Firmware reference 1"]) expect(markup).toContain(label);
    for (const label of ["Embrix Status", "Activity", "CURRENT DESIGN TRUTH", "PROJECT KNOWLEDGE", "暂无已确认内容"]) expect(markup).not.toContain(label);
    for (const internal of ["AVAILABLE", "UNKNOWN", "RequirementContext", "fingerprint", "CONFIRMED"]) expect(markup).not.toContain(internal);
    for (const internal of ["hardware-spec-internal", "architecture_fingerprint", "provenance", "raw JSON", "Generate PCB", "Export KiCad", "Apply Hardware Changes", "Build", "Flash"]) expect(markup).not.toContain(internal);
  });

  it("renders a safe empty hardware design state without execution controls", () => {
    const markup = renderToStaticMarkup(<EngineeringContextPanel identity={toProjectWorkspaceIdentity(project, null)} project={project} context={context} projection={{ projectId: project.project_id, references: [], evidenceSources: [], resources: [] }} memoryProjection={null} resources={[]} workflow={null} hardwareDesignSpec={null} />);
    expect(markup).not.toContain("HARDWARE DESIGN");
    expect(markup).not.toContain("暂无硬件设计规格");
    expect(markup).not.toContain("Generate PCB");
    expect(markup).not.toContain("Apply Hardware Changes");
  });

  it("projects design and verification planning without execution controls", () => {
    const markup = renderToStaticMarkup(<EngineeringContextPanel identity={toProjectWorkspaceIdentity(project, null)} project={project} context={context} projection={{ projectId: project.project_id, references: [], evidenceSources: [], resources: [] }} memoryProjection={null} resources={[]} workflow={null} hardwareDesignSpec={hardwareDesignSpec} firmwareSpec={firmwareSpec} simulationSpec={simulationSpec} verificationSpec={verificationSpec} implementationSpec={implementationSpec} capabilityEvaluation={capabilityEvaluation} executionAuthorization={executionAuthorization} />);
    for (const label of ["FIRMWARE DESIGN", "Firmware Architecture", "Application", "Sensor Acquisition", "Motor Control", "Hardware Binding", "Controller → Sensor", "Verification", "Review and software test", "Pending"]) expect(markup).toContain(label);
    for (const label of ["SIMULATION DESIGN", "Simulation Targets", "Scenarios", "Evidence", "Controller to Sensor Interface Test", "Interface Test", "Pending evidence for interface test"]) expect(markup).toContain(label);
    expect(markup.indexOf("ENGINEERING ROADMAP")).toBeLessThan(markup.indexOf("HARDWARE DESIGN"));
    expect(markup.indexOf("HARDWARE DESIGN")).toBeLessThan(markup.indexOf("FIRMWARE DESIGN"));
    expect(markup.indexOf("FIRMWARE DESIGN")).toBeLessThan(markup.indexOf("SIMULATION DESIGN"));
    for (const label of ["VERIFICATION PLAN", "Verification Cases", "Motor Control Verification", "Control", "Evidence Required", "Waveform", "Status", "Pending"]) expect(markup).toContain(label);
    expect(markup.indexOf("SIMULATION DESIGN")).toBeLessThan(markup.indexOf("VERIFICATION PLAN"));
    for (const label of ["IMPLEMENTATION PLAN", "Implementation Tasks", "Implement Motor Control", "Expected Outputs", "Motor Control Firmware Module", "Verification", "Motor Control Verification", "Control Implementation", "Planned"]) expect(markup).toContain(label);
    expect(markup.indexOf("VERIFICATION PLAN")).toBeLessThan(markup.indexOf("IMPLEMENTATION PLAN"));
    for (const label of ["ENGINEERING CAPABILITIES", "Firmware Code Generation", "Artifact Creation", "Firmware", "Artifact", "High", "Low", "Required · Pending", "Not Required", "Planned"]) expect(markup).toContain(label);
    expect(markup.indexOf("IMPLEMENTATION PLAN")).toBeLessThan(markup.indexOf("ENGINEERING CAPABILITIES"));
    for (const label of ["EXECUTION AUTHORIZATION", "Firmware Code Generation", "Artifact Creation", "Flash Device", "Risk: High", "Risk: Low", "Risk: Critical", "Pending Approval", "Pending Authorization", "Pending Review"]) expect(markup).toContain(label);
    expect(markup.indexOf("ENGINEERING CAPABILITIES")).toBeLessThan(markup.indexOf("EXECUTION AUTHORIZATION"));
    for (const internal of ["firmware-spec-internal", "simulation-spec-internal", "verification-spec-internal", "implementation-spec-internal", "capability-evaluation-internal", "execution-authorization-internal", "request-firmware-internal", "policy-firmware-internal", "decision-firmware-internal", "FIRMWARE_CODE_GENERATION", "ARTIFACT_CREATION", "implementation-task-motor", "approval-internal", "requested_by", "request_reason", "schema_version", "hardware_design_fingerprint", "firmware_spec_fingerprint", "simulation_spec_fingerprint", "verification_fingerprint", "fingerprint", "source_refs", "provenance", "source_path", "raw JSON", "Generate Code", "Create Files", "Build Firmware", "Apply Changes", "Run Simulation", "Generate Model", "Export Wokwi", "Start Runtime", "Run Test", "Execute", "Collect Result", "Approve Result"]) expect(markup).not.toContain(internal);
  });

  it("shows only firmware authorization decisions and a safe two-file proposal", () => {
    const authorizationMarkup = renderToStaticMarkup(<EngineeringContextPanel identity={toProjectWorkspaceIdentity(project, null)} project={project} context={context} projection={{ projectId: project.project_id, references: [], evidenceSources: [], resources: [] }} memoryProjection={null} resources={[]} workflow={null} capabilityEvaluation={capabilityEvaluation} executionAuthorization={executionAuthorization} onAuthorizationDecision={() => undefined} />);
    expect(authorizationMarkup).toContain("Approve");
    expect(authorizationMarkup).toContain("Reject");
    expect(authorizationMarkup.match(/>Approve</g)).toHaveLength(1);

    const proposalMarkup = renderToStaticMarkup(<FirmwareSourceProposalCard generation={firmwareSourceGeneration} onDecision={() => undefined} />);
    for (const label of ["Firmware Source Proposal", "Motor Control", "generated/motor_control.h", "generated/motor_control.c", "Awaiting Approval", "Approve Generation", "Reject", "Firmware evidence parse: Unavailable", "Verification references: 1"]) expect(proposalMarkup).toContain(label);
    for (const internal of ["source-generation-request-internal", "source-proposal-internal", "request-firmware-internal", "implementation-task-motor", "firmware-module-motor", "sha256:", "content_fingerprint", "requester", "raw JSON", "Build", "Flash", "Simulation", "Hardware"]) expect(proposalMarkup).not.toContain(internal);
  });

  it("renders a safe empty firmware design state", () => {
    const markup = renderToStaticMarkup(<EngineeringContextPanel identity={toProjectWorkspaceIdentity(project, null)} project={project} context={context} projection={{ projectId: project.project_id, references: [], evidenceSources: [], resources: [] }} memoryProjection={null} resources={[]} workflow={null} hardwareDesignSpec={null} firmwareSpec={null} />);
    expect(markup).not.toContain("FIRMWARE DESIGN");
    expect(markup).not.toContain("暂无固件设计规格");
    expect(markup).not.toContain("Generate Code");
    expect(markup).not.toContain("Flash Device");
  });

  it("projects simulation design planning without execution controls", () => {
    const markup = renderToStaticMarkup(<EngineeringContextPanel identity={toProjectWorkspaceIdentity(project, null)} project={project} context={context} projection={{ projectId: project.project_id, references: [], evidenceSources: [], resources: [] }} memoryProjection={null} resources={[]} workflow={null} hardwareDesignSpec={hardwareDesignSpec} firmwareSpec={firmwareSpec} simulationSpec={simulationSpec} />);
    for (const label of ["SIMULATION DESIGN", "Simulation Targets", "Controller", "Firmware Module", "Scenarios", "Interface Test", "Evidence", "Pending"]) expect(markup).toContain(label);
    for (const internal of ["simulation-spec-internal", "hardware_design_fingerprint", "firmware_spec_fingerprint", "provenance", "source_reference", "raw JSON", "Run Simulation", "Generate Model", "Export Renode", "Start Runtime"]) expect(markup).not.toContain(internal);
  });

  it("renders a safe empty simulation design state", () => {
    const markup = renderToStaticMarkup(<EngineeringContextPanel identity={toProjectWorkspaceIdentity(project, null)} project={project} context={context} projection={{ projectId: project.project_id, references: [], evidenceSources: [], resources: [] }} memoryProjection={null} resources={[]} workflow={null} simulationSpec={null} />);
    expect(markup).not.toContain("SIMULATION DESIGN");
    expect(markup).not.toContain("暂无仿真设计规格");
    expect(markup).not.toContain("Run Simulation");
    expect(markup).not.toContain("Start Runtime");
  });

  it("renders a safe empty verification plan without execution controls", () => {
    const markup = renderToStaticMarkup(<EngineeringContextPanel identity={toProjectWorkspaceIdentity(project, null)} project={project} context={context} projection={{ projectId: project.project_id, references: [], evidenceSources: [], resources: [] }} memoryProjection={null} resources={[]} workflow={null} verificationSpec={null} />);
    expect(markup).not.toContain("VERIFICATION PLAN");
    expect(markup).not.toContain("暂无验证规划");
    for (const control of ["Run Test", "Execute", "Collect Result", "Approve Result"]) expect(markup).not.toContain(control);
  });

  it("renders a safe empty implementation plan without mutation controls", () => {
    const markup = renderToStaticMarkup(<EngineeringContextPanel identity={toProjectWorkspaceIdentity(project, null)} project={project} context={context} projection={{ projectId: project.project_id, references: [], evidenceSources: [], resources: [] }} memoryProjection={null} resources={[]} workflow={null} implementationSpec={null} />);
    expect(markup).not.toContain("IMPLEMENTATION PLAN");
    expect(markup).not.toContain("暂无实现规划");
    for (const control of ["Generate Code", "Create Files", "Build", "Flash", "Apply Changes"]) expect(markup).not.toContain(control);
  });

  it("renders a safe empty capability evaluation without execution controls", () => {
    const markup = renderToStaticMarkup(<EngineeringContextPanel identity={toProjectWorkspaceIdentity(project, null)} project={project} context={context} projection={{ projectId: project.project_id, references: [], evidenceSources: [], resources: [] }} memoryProjection={null} resources={[]} workflow={null} capabilityEvaluation={null} />);
    expect(markup).not.toContain("ENGINEERING CAPABILITIES");
    expect(markup).not.toContain("暂无能力评估");
    expect(markup).not.toContain("EXECUTION AUTHORIZATION");
    expect(markup).not.toContain("暂无执行授权状态");
    for (const control of ["Execute", "Generate Code", "Build", "Flash", "Apply", "Run"]) expect(markup).not.toContain(control);
  });

  it("projects the current requirement without leaking internal contract fields", () => {
    const markup = renderToStaticMarkup(<EngineeringContextPanel identity={toProjectWorkspaceIdentity(project, null)} project={project} context={context} projection={{ projectId: project.project_id, references: [], evidenceSources: [], resources: [] }} memoryProjection={null} resources={[]} workflow={null} requirement={requirement} />);
    for (const label of ["CURRENT REQUIREMENT", "STM32 智能小车", "待澄清", "3/4 项已确认", "巡线", "请确认供电方式。", "验收标准", "Given", "验证：轨迹测试"]) expect(markup).toContain(label);
    for (const internal of ["CONFIRMED", "PROPOSED", "CLARIFICATION_REQUIRED", "sha256:", "RequirementContext", "TARGET_PLATFORM"]) expect(markup).not.toContain(internal);
  });

  it("projects the current plan without exposing internal metadata", () => {
    const markup = renderToStaticMarkup(<EngineeringContextPanel identity={toProjectWorkspaceIdentity(project, null)} project={project} context={context} projection={{ projectId: project.project_id, references: [], evidenceSources: [], resources: [] }} memoryProjection={null} resources={[]} workflow={null} requirement={requirement} plan={plan} />);
    for (const label of ["CURRENT PLAN", "STM32 Smart Car Development Plan", "待用户审批", "1/4 个阶段已完成", "System Architecture", "Hardware Design"]) expect(markup).toContain(label);
    for (const internal of ["PROPOSED", "sha256:", "EngineeringPlan", "provider", "raw JSON"]) expect(markup.toLowerCase()).not.toContain(internal.toLowerCase());
  });

  it("projects a minimal current architecture without document-flow internals", () => {
    const markup = renderToStaticMarkup(<EngineeringContextPanel identity={toProjectWorkspaceIdentity(project, null)} project={project} context={context} projection={{ projectId: project.project_id, references: [], evidenceSources: [], resources: [] }} memoryProjection={null} resources={[]} workflow={null} requirement={requirement} plan={plan} architecture={architecture} />);
    for (const label of ["CURRENT ARCHITECTURE", "STM32 Smart Car Architecture", "待用户审批", "Architecture Summary", "STM32", "Interfaces", "待明确"]) expect(markup).toContain(label);
    expect(markup).not.toContain("Data Flow");
    expect(markup).not.toContain("Control Flow");
    for (const internal of ["PROPOSED", "sha256:", "provenance", "raw JSON", "provider"]) expect(markup.toLowerCase()).not.toContain(internal.toLowerCase());
  });

  it("projects design truth into confirmed, suggested and unavailable mappings", () => {
    const markup = renderToStaticMarkup(<EngineeringContextPanel identity={toProjectWorkspaceIdentity(project, null)} project={project} context={context} projection={{ projectId: project.project_id, references: [], evidenceSources: [], resources: [] }} memoryProjection={null} resources={[]} workflow={null} architecture={architecture} designTruth={designTruth} />);
    for (const label of ["CURRENT DESIGN TRUTH", "设计版本", "Confirmed Mapping", "组件：STM32", "Suggested Mapping", "组件：Control", "Unavailable", "Hardware Mapping：暂无已确认硬件映射，需要原理图或 PCB 证据", "Firmware Mapping：没有 Firmware 证据", "部分接口协议待确认"]) expect(markup).toContain(label);
    for (const internal of ["truth-1", "design-controller", "evidence-1", "source_id", "provenance", "sha256:", "raw JSON"]) expect(markup.toLowerCase()).not.toContain(internal.toLowerCase());
  });

  it("projects only safe, read-only knowledge labels", () => {
    const markup = renderToStaticMarkup(<EngineeringContextPanel identity={toProjectWorkspaceIdentity(project, null)} project={project} context={context} projection={{ projectId: project.project_id, references: [], evidenceSources: [], resources: [] }} memoryProjection={null} resources={[]} workflow={null} engineeringKnowledgeModel={knowledgeModel} />);
    for (const label of ["PROJECT KNOWLEDGE", "Subsystems", "Motor Control", "Learning Topics", "I2C Communication"]) expect(markup).toContain(label);
    for (const label of ["System", "Modification Points", "暂无自动生成改造建议"]) expect(markup).not.toContain(label);
    for (const internal of ["knowledge-internal", "entity-subsystem-internal", "topic-internal", "sha256:", "provenance", "interface-i2c", "binding-internal", "Apply", "Build", "Teach"]) expect(markup.toLowerCase()).not.toContain(internal.toLowerCase());
  });

  it("projects learning context without exposing generation or source internals", () => {
    const markup = renderToStaticMarkup(<EngineeringContextPanel identity={toProjectWorkspaceIdentity(project, null)} project={project} context={context} projection={{ projectId: project.project_id, references: [], evidenceSources: [], resources: [] }} memoryProjection={null} resources={[]} workflow={null} teachingContext={teachingContext} />);
    for (const label of ["LEARNING CONTEXT", "Subject", "Motor Control", "Learning Mode", "Engineering", "Learning Path", "Component", "Sources", "Confirmed Knowledge"]) expect(markup).toContain(label);
    for (const internal of ["teaching-context-internal", "teaching-subject-internal", "teaching-step-internal", "knowledge-internal", "binding-internal", "sha256:", "source_knowledge", "provenance", "raw JSON", "Apply", "Build", "Teach"]) expect(markup.toLowerCase()).not.toContain(internal.toLowerCase());
  });

  it("projects the canonical engineering roadmap as read-only safe state", () => {
    const markup = renderToStaticMarkup(<EngineeringRoadmap workflow={engineeringWorkflow} />);
    for (const label of ["ENGINEERING ROADMAP", "Current Stage", "Hardware Design", "Progress", "1 / 2", "Next", "Design power circuit", "Hardware Design Proposal", "待开始"]) expect(markup).toContain(label);
    for (const internal of ["workflow-internal", "workflow-phase-2", "artifact-internal", "plan-output-1", "sha256:", "provenance", "Execute", "Build", "Flash", "Apply"]) expect(markup.toLowerCase()).not.toContain(internal.toLowerCase());
  });

  it("projects artifact evidence lifecycle without exposing parser internals", () => {
    const projection = {
      files: [{ name: "robot.kicad_sch", type: "SCHEMATIC", status: "PARSED" }],
      coverage: { schematic: "PARSED", pcb: "MISSING", bom: "MISSING", firmware: "MISSING", gerber: "MISSING", documentation: "MISSING", completeness_percent: 17 },
      available_evidence: ["U1 STM32F103"],
      evidence_review_status: "CANDIDATE" as const,
      missing_evidence: ["缺少 Firmware 源码"],
    };
    const markup = renderToStaticMarkup(<EngineeringContextPanel identity={toProjectWorkspaceIdentity(project, null)} project={project} context={context} projection={{ projectId: project.project_id, references: [], evidenceSources: [], resources: [] }} memoryProjection={null} resources={[]} workflow={null} artifactIntelligence={projection} />);
    for (const label of ["PROJECT ARTIFACTS", "robot.kicad_sch", "Available Evidence", "U1 STM32F103", "待审核", "缺少 Firmware 源码"]) expect(markup).toContain(label);
    for (const internal of ["sha256:", "path_reference", "artifact_id", "provenance", "confidence", "Mode", "Intent"]) expect(markup.toLowerCase()).not.toContain(internal.toLowerCase());
  });

  it("keeps the project-context fixture aligned across the three IDE regions", () => {
    const fixture = projectContextWorkspaceFixture;
    const panel = renderToStaticMarkup(<RuntimePanel project={fixture.project} context={fixture.context} panel={null} serviceError={null} />);
    const chat = renderToStaticMarkup(<ChatPanel title="Copilot 工程分析" projectName={fixture.project?.name} sessionReady={false} turns={[]} pendingQuestion={null} availableReferences={fixture.references} pending={false} error={null} selected={[]} evidenceDisplay="expanded" onToggleReference={() => undefined} onAttach={() => undefined} onSubmit={() => undefined} onRetry={() => undefined} />);
    expect(panel).toContain("循迹智能小车");
    for (const count of ["Firmware", "3 references", "Hardware", "2 references", "Documents", "5 references", "HIL", "2", "Memory", "1"]) expect(panel).toContain(count);
    expect(chat).toContain("告诉 Embrix 你想做什么。");
    expect(chat).toContain("从需求开始，我会和你一起完成工程。");
    expect(chat).not.toContain("从一个工程目标开始");
    expect(chat).toContain('placeholder="请先选择 Project 或完成环境配置"');
    expect(chat).not.toContain("询问关于「循迹智能小车」的工程问题...");
    expect(panel).not.toContain("Project ID");
    expect(panel).not.toContain("循迹智能小车/");
  });

  it("fails closed with a useful project selection empty state", () => {
    const markup = renderToStaticMarkup(<ChatPanel title="Copilot 工程分析" projectName={null} sessionReady={false} turns={[]} pendingQuestion={null} availableReferences={[]} pending={false} error="Project Catalog 不可用" selected={[]} evidenceDisplay="expanded" onToggleReference={() => undefined} onAttach={() => undefined} onSubmit={() => undefined} onRetry={() => undefined} />);
    expect(markup).toContain("告诉 Embrix 你想做什么。");
    expect(markup).toContain("从需求开始，我会和你一起完成工程。");
    expect(markup).toContain("这次处理没有完成");
    expect(markup).not.toContain("Project Catalog 不可用");
  });

  it("does not expose engineering mutation controls", () => {
    const markup = renderToStaticMarkup(<ChatPanel title="分析" {...chatProps} />);
    for (const forbidden of ["Apply Fix", "Execute", "Flash", "Build", "Repair", "raw log", "hidden reasoning", "API Key"]) expect(markup.toLowerCase()).not.toContain(forbidden.toLowerCase());
  });

  it("projects answer contract failures without generic analysis wording", () => {
    const markup = renderToStaticMarkup(
      <AssistantMessage
        answer={null}
        evidence={[]}
        recommendations={[]}
        pending={false}
        failed
        errorCode="ANSWER_SCHEMA_INVALID"
        analysisState="FAILED"
        evidenceDisplay="compact"
      />,
    );
    expect(markup).toContain("这次回答没有通过格式校验");
    expect(markup).not.toContain("工程分析执行失败");
  });

  it("uses project-first language for the creation entry", () => {
    const markup = renderToStaticMarkup(<ProjectCreatePanel onClose={() => undefined} onCreated={() => undefined} />);
    expect(markup).toContain("新建项目");
    expect(markup).toContain("创建一个新的嵌入式研发项目");
    expect(markup).not.toContain("Create Workspace");
    expect(markup).not.toContain("新建 Workspace");
  });
});
