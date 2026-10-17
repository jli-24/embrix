import { DESKTOP_SESSION_EXPIRED, isDesktopSessionExpired, mapDesktopSessionError, readDesktopSessionErrorCode } from "./sessionState";
import {
  recordFrontendProviderDiagnostic,
  webViewDiagnosticOriginHeaders,
  type FrontendProviderDiagnosticEvent,
  type FrontendProviderDiagnosticSink,
} from "./webviewSessionDiagnostic";

export type ProjectType = "ESP_IDF" | "STM32" | "ARDUINO" | "GENERIC_FIRMWARE" | "EXISTING_FIRMWARE";
export type ProjectCreationMode = "CREATE_NEW" | "ATTACH_EXISTING";
export type ProjectStatus = "PENDING" | "INITIALIZING" | "READY" | "FAILED";
export type CanonicalRoute = "GREENFIELD_DISCOVERY" | "GREENFIELD_DESIGN" | "BROWNFIELD_ANALYSIS" | "DEBUG_ANALYSIS" | "KNOWLEDGE" | "CLARIFICATION_REQUIRED";
export type AnalysisState = "REQUESTED" | "CONTEXT_READY" | "WAITING_FOR_CLARIFICATION" | "PROVIDER_UNAVAILABLE" | "INSUFFICIENT_EVIDENCE" | "COMPLETED" | "FAILED" | "DISCOVERY_STARTED" | "REQUIREMENTS_PARTIAL" | "REQUIREMENTS_REFINING" | "REQUIREMENTS_READY" | "DESIGN_READY";
export type ConversationResponseType = "CHAT" | "DOCUMENT" | "ARTIFACT";
export type TruthLevel = "EXPLICIT" | "INFERRED" | "UNKNOWN" | "INVALIDATED";
export type KnowledgeConfidence = "HIGH" | "MEDIUM" | "LOW";
export type EngineeringKnowledgeEntityType = "SYSTEM" | "SUBSYSTEM" | "COMPONENT" | "INTERFACE" | "SIGNAL" | "FUNCTION" | "DESIGN_DECISION" | "RISK" | "LEARNING_TOPIC";
export type EngineeringKnowledgeRelationshipType = "CONTAINS" | "CONNECTS" | "CONTROLS" | "DEPENDS_ON" | "IMPLEMENTS" | "VERIFIED_BY" | "MODIFIED_BY";
export type EngineeringWorkflowStatus = "DRAFT" | "PLANNED" | "IN_PROGRESS" | "BLOCKED" | "COMPLETED" | "CANCELLED";
export type EngineeringWorkflowItemStatus = "PENDING" | "IN_PROGRESS" | "COMPLETED" | "BLOCKED" | "CANCELLED";
export type EngineeringWorkflowPhaseType = "REQUIREMENT_CONFIRMATION" | "SYSTEM_DESIGN" | "HARDWARE_DESIGN" | "PCB_DESIGN" | "FIRMWARE_DEVELOPMENT" | "SIMULATION" | "VERIFICATION";
export type EngineeringWorkflowTaskType = "ANALYSIS" | "DESIGN" | "IMPLEMENTATION" | "REVIEW" | "TEST" | "DOCUMENTATION";
export type EngineeringWorkflowArtifactType = "DOCUMENT" | "SCHEMATIC" | "PCB" | "FIRMWARE" | "CONFIGURATION" | "SIMULATION_RESULT" | "TEST_REPORT";
export type LearningMode = "OVERVIEW" | "ENGINEERING" | "DEEP_DIVE";
export type TeachingScope = LearningMode;
export type TeachingDifficulty = "FOUNDATION" | "INTERMEDIATE" | "ADVANCED";
export type TeachingStepType = "SYSTEM_OVERVIEW" | "ARCHITECTURE" | "COMPONENT" | "INTERFACE" | "SIGNAL" | "VERIFICATION";
export type TeachingSubjectEntityType = "SYSTEM" | "SUBSYSTEM" | "COMPONENT" | "INTERFACE" | "SIGNAL" | "FUNCTION" | "DESIGN_DECISION";
export type DesignSource = "ARCHITECTURE" | "SCHEMATIC" | "PCB" | "GERBER" | "BOM" | "FIRMWARE" | "DATASHEET" | "README" | "USER_INPUT";
export type EngineeringEvidenceReviewStatus = "UNAVAILABLE" | "CANDIDATE" | "REVIEWING" | "APPROVED" | "REJECTED" | "SUPERSEDED" | "INVALIDATED";

export type ProjectView = {
  project_id: string;
  tenant_id: string;
  name: string;
  location_id: string;
  directory_name: string;
  creation_mode: ProjectCreationMode;
  relative_path: string;
  project_type: ProjectType;
  initialization_status: ProjectStatus;
  platform: string | null;
  version: string | null;
  branch: string | null;
  failure_code: string | null;
  created_at: string;
  updated_at: string;
  last_activity_at: string;
  fingerprint: string;
};

export type ProjectPreview = {
  preview_id: string;
  name: string;
  location: ProjectLocation;
  existing_directory: ProjectDirectory | null;
  directory_name: string;
  creation_mode: ProjectCreationMode;
  relative_path: string;
  project_type: ProjectType;
  mode: "CREATE" | "ATTACH";
  expires_at: string;
  write_summary: string[];
  fingerprint: string;
};

export type EdaImportBackend = "KICAD" | "JLCEDA_PRO";
export type EdaImportIssue = { kind: "UNKNOWN" | "CONFLICT" | "LOSS"; code: string; locator: string; blocks_mutation: boolean };
export type EdaImportFile = {
  relative_path: string;
  kind: "FILE" | "MISSING_DEPENDENCY" | "EXTERNAL_DEPENDENCY";
  byte_size: number | null;
  sha256: string | null;
  reference_digest: string | null;
  discovery_locator: string | null;
};
export type EdaImportObject = {
  object_type: "FOOTPRINT";
  source_file: string;
  native_id: string;
  locator: string;
  library_id: string;
  reference: string | null;
  position_x_mm: number;
  position_y_mm: number;
  rotation_degrees: number;
};
export type CoverageNativeKind = "PROJECT_SETTINGS" | "PCB_NET" | "FOOTPRINT" | "PAD" | "TRACK_SEGMENT" | "VIA" | "ZONE" | "BOARD_GRAPHIC" | "BOARD_TEXT" | "SCHEMATIC_SYMBOL" | "SHEET" | "WIRE" | "JUNCTION" | "NO_CONNECT" | "LABEL" | "POLYLINE" | "TEXT" | "SYMBOL_DEFINITION" | "FOOTPRINT_DEFINITION" | "MODEL_3D_REF" | "LIBRARY_ENTRY" | "PROPERTY" | "UNKNOWN";
export type CoverageDependencyState = "RESOLVED_IN_SOURCE" | "EXTERNAL_UNVERIFIED" | "MISSING" | "UNSUPPORTED";
export type CoverageSourceSpanV1 = { byte_start: number; byte_length: number; sha256: string };
export type CoverageFileV1 = {
  relative_path: string; byte_size: number; sha256: string;
  role: "PROJECT_SETTINGS" | "SCHEMATIC" | "PCB" | "SYMBOL_LIBRARY" | "FOOTPRINT_LIBRARY_MEMBER" | "MODEL_3D" | "LIBRARY_TABLE" | "OTHER_SOURCE";
  format_marker: string | null; parse_status: "PARSED" | "OPAQUE" | "UNSUPPORTED" | "FAILED";
};
export type CoverageObjectV1 = {
  source_file: string; locator: string; parent_locator: string | null; native_kind: CoverageNativeKind;
  native_id: string | null; keyword: string; values: string[]; child_locators: string[]; source_span: CoverageSourceSpanV1 | null;
};
export type CoverageRelationV1 = {
  relation_type: "CONTAINS" | "INSTANCE_OF" | "USES_LIBRARY" | "USES_MODEL" | "SHEET_PATH" | "CONNECTS_NET";
  source_file: string; source_locator: string; target_file: string | null; target_locator: string | null;
  target_reference: string; state: CoverageDependencyState;
};
export type CoverageDependencyV1 = {
  source_file: string; source_locator: string; reference: string;
  kind: "SCHEMATIC_SHEET" | "SYMBOL_LIBRARY" | "FOOTPRINT_LIBRARY" | "MODEL_3D";
  resolved_relative_path: string | null; state: CoverageDependencyState;
};
export type CoverageUnknownV1 = {
  source_file: string; locator: string; native_keyword: string; source_file_sha256: string; source_span: CoverageSourceSpanV1 | null;
  reason: "UNKNOWN_KEYWORD" | "UNKNOWN_FORMAT" | "OPAQUE_GEOMETRY" | "PARSE_FAILURE" | "UNSUPPORTED_SEMANTICS";
};
export type CoverageIssueV1 = {
  code: "ENTRY_MISSING" | "FORMAT_UNSUPPORTED" | "PARSE_FAILURE" | "OBJECT_UNKNOWN" | "MODEL_GEOMETRY_OPAQUE" | "DEPENDENCY_EXTERNAL" | "DEPENDENCY_MISSING" | "RELATION_UNRESOLVED" | "SOURCE_CHANGED";
  source_file: string; locator: string | null; blocks_complete: boolean;
};
export type ProjectCoverageV1 = {
  schema: "kicad-project-source-coverage-v1"; profile_id: "KICAD_PROJECT_SOURCE_20240108_V1"; source_set_fingerprint: string;
  entry_files: { project: string; schematic_root: string; board: string };
  files: CoverageFileV1[]; objects: CoverageObjectV1[]; relations: CoverageRelationV1[];
  dependencies: CoverageDependencyV1[]; unknowns: CoverageUnknownV1[];
  counts: { objects_total: number; recognized_total: number; unknown_total: number; by_kind: { native_kind: CoverageNativeKind; count: number }[] };
  coverage_state: "COMPLETE_FOR_DECLARED_SOURCE_OBJECTS" | "PARTIAL" | "UNKNOWN";
  dependency_state: "RESOLVED_IN_SOURCE" | "EXTERNAL_UNVERIFIED" | "MISSING" | "UNKNOWN";
  issues: CoverageIssueV1[]; fingerprint: string;
};

export type EdaImportPreviewInput = {
  backend: EdaImportBackend;
  native_entry_relative_path: string;
  selected_native_entries: string[];
  expected_head_revision_id: string | null;
  expected_head_revision_fingerprint: string | null;
};
export type EdaImportPreview = {
  project_coverage?: ProjectCoverageV1;
  status: "PREVIEW";
  preview_id: string;
  preview_fingerprint: string;
  expires_at: string;
  backend: EdaImportBackend;
  native_entry_relative_path: string;
  native_format_family: string;
  native_format_versions: Record<string, string>;
  source_set_fingerprint: string;
  parser_ruleset_id: string;
  supported_subset_id: string;
  semantic_completeness: "COMPLETE_FOR_DECLARED_SUBSET" | "PARTIAL" | "UNKNOWN";
  manifest: EdaImportFile[];
  objects: EdaImportObject[];
  issues: EdaImportIssue[];
  verified: false;
  can_modify: false;
};
export type EdaImportRevisionSummary = {
  import_revision_id: string;
  import_revision_fingerprint: string;
  active: boolean;
  created_at: string;
  backend: EdaImportBackend;
  native_entry_relative_path: string;
  source_set_fingerprint: string;
};
export type EdaImportRevision = {
  project_coverage?: ProjectCoverageV1;
  import_revision_id: string;
  import_revision_fingerprint: string;
  active: boolean;
  created_at: string;
  backend: EdaImportBackend;
  native_entry_relative_path: string;
  native_format_family: string;
  native_format_versions: Record<string, string>;
  source_set_fingerprint: string;
  parser_ruleset_id: string;
  supported_subset_id: string;
  source_currentness: "CURRENT" | "STALE" | "UNAVAILABLE";
  observation_currentness: "CURRENT" | "STALE" | "UNAVAILABLE";
  semantic_completeness: "COMPLETE_FOR_DECLARED_SUBSET" | "PARTIAL" | "UNKNOWN";
  manifest: EdaImportFile[];
  objects: EdaImportObject[];
  issues: EdaImportIssue[];
  verified: false;
  can_modify: false;
};
export type EdaImportCommit = EdaImportRevision & {
  status: "IMPORTED_OBSERVATION";
  commit_outcome: "CREATED" | "REUSED_ACTIVE" | "ALREADY_COMMITTED";
};

export type ProjectLocation = {
  location_id: string;
  display_label: string;
  availability: string;
  path_leaf?: string | null;
  /** Optional safe display metadata supplied by a newer projection. */
  path?: string | null;
  project_name?: string | null;
  project_type?: ProjectType | string | null;
  fingerprint: string;
};

export type ProjectDeletionResult = {
  project_id: string;
  project_fingerprint: string;
  deleted_at: string;
  operation: "PROJECT_DELETED";
  fingerprint: string;
};

export type ProjectDirectory = {
  directory_id: string;
  display_name: string;
  fingerprint: string;
};

export type ProjectContextReference = {
  reference_id: string;
  category: string;
  display_label: string;
  source_fingerprint: string;
  fingerprint: string;
};

export type ProjectContext = {
  project_id: string;
  status: ProjectStatus;
  groups: Array<{ category: string; references: ProjectContextReference[] }>;
  evidence_sources: Array<{ source: string; count: number }>;
  capabilities: Array<{ capability: string; status: string }>;
  generated_at: string;
  fingerprint: string;
};

export type ConversationTurn = {
  turn_id: string;
  request_id: string;
  trace_id?: string | null;
  route?: CanonicalRoute | "GREENFIELD" | "BROWNFIELD" | "FIRMWARE" | "DEBUG" | "UNKNOWN" | null;
  continuation_of_turn_id?: string | null;
  previous_analysis_state?: string | null;
  previous_route?: string | null;
  previous_intent?: string | null;
  status: "PENDING" | "WAITING_FOR_CLARIFICATION" | "COMPLETED" | "FAILED";
  question: string;
  answer_text: string | null;
  response_type?: ConversationResponseType;
  artifact_refs?: string[];
  evidence: ChatEvidence[];
  recommendations: ChatRecommendation[];
  context_references: unknown[];
  error_code: string | null;
  analysis_state?: AnalysisState;
  analysis_reason_code?: string | null;
  terminal_event?: "ANSWER_COMPLETED" | "ANSWER_FAILED" | null;
  created_at: string;
  completed_at: string | null;
};

export type ChatEvidence = {
  source_type: string;
  label: string;
  reference_id: string;
  status: string;
  summary: string | null;
  source_uri?: string | null;
  source_fingerprint: string;
  observed_at: string;
};

export type ChatRecommendation = {
  review: string;
  reason: string | null;
  status: string;
  reference_id: string;
  source_fingerprint: string;
};

export type HardwareDomainType = "SYSTEM" | "CONTROLLER" | "POWER" | "SENSOR" | "ACTUATOR" | "COMMUNICATION" | "MECHANICAL" | "OTHER";
export type HardwareCandidateType = "CAPABILITY" | "DEVICE" | "MODULE" | "UNKNOWN";
export type HardwareCandidateStatus = "PROPOSED" | "CONFIRMED" | "REJECTED" | "UNKNOWN";
export type HardwareDecisionStatus = "OPEN" | "CONFIRMED" | "REJECTED";
export type HardwareInterfaceType = "POWER" | "GPIO" | "BUS" | "CONTROL" | "CLOCK" | "DEBUG" | "COMMUNICATION" | "ANALOG" | "DIGITAL" | "UNKNOWN";
export type SchematicCircuitType = "POWER_CIRCUIT" | "CONTROLLER_CIRCUIT" | "INTERFACE_CIRCUIT" | "SENSOR_CIRCUIT" | "ACTUATOR_CIRCUIT" | "COMMUNICATION_CIRCUIT";
export type HardwareDesignPlanStatus = "PLANNED" | "REVIEWING" | "APPROVED" | "BLOCKED";

export type HardwareDomain = {
  domain_id: string;
  hardware_spec_id: string;
  name: string;
  type: HardwareDomainType;
  requirements: string[];
  interfaces: string[];
  verification_refs: string[];
  fingerprint: string;
};

export type HardwareComponentCandidate = {
  candidate_id: string;
  domain_id: string;
  candidate_type: HardwareCandidateType;
  category: string;
  name: string;
  description: string;
  selection_status: HardwareCandidateStatus;
  constraints: string[];
  evidence_refs: string[];
  fingerprint: string;
};

export type HardwareDecision = {
  decision_id: string;
  subject: string;
  options: string[];
  selected_option: string | null;
  rationale: string;
  status: HardwareDecisionStatus;
  evidence_refs: string[];
  fingerprint: string;
};

export type HardwareInterface = {
  interface_id: string;
  source: string;
  target: string;
  interface_type: HardwareInterfaceType;
  signal_refs: string[];
  direction: string;
  constraints: string[];
  evidence_refs: string[];
  fingerprint: string;
};

export type SchematicDesignPlan = {
  schematic_plan_id: string;
  hardware_spec_id: string;
  circuits: SchematicCircuitType[];
  status: HardwareDesignPlanStatus;
  constraints: string[];
  verification_refs: string[];
  fingerprint: string;
};

export type PCBDesignPlan = {
  pcb_plan_id: string;
  hardware_spec_id: string;
  status: HardwareDesignPlanStatus;
  constraints: string[];
  layout_requirements: string[];
  verification_refs: string[];
  fingerprint: string;
};

export type HardwareDesignSpecification = {
  hardware_spec_id: string;
  project_id: string;
  architecture_id: string;
  workflow_id: string;
  architecture_fingerprint: string;
  workflow_fingerprint: string;
  design_revision: string;
  title: string;
  system_requirements: string[];
  hardware_domains: HardwareDomain[];
  component_candidates: HardwareComponentCandidate[];
  interfaces: HardwareInterface[];
  decisions: HardwareDecision[];
  constraints: string[];
  verification_requirements: string[];
  source_refs: string[];
  provenance: unknown[];
  schematic_plan: SchematicDesignPlan | null;
  pcb_plan: PCBDesignPlan | null;
  fingerprint: string;
};

export type FirmwareModuleType = "APPLICATION" | "CONTROL" | "DRIVER" | "HAL" | "COMMUNICATION" | "MIDDLEWARE" | "BOOT" | "UNKNOWN";
export type FirmwareInterfaceRole = "INPUT" | "OUTPUT" | "CONTROL" | "COMMUNICATION" | "POWER_STATE" | "UNKNOWN";
export type FirmwareTaskType = "DESIGN" | "DRIVER" | "IMPLEMENTATION" | "INTEGRATION" | "TEST" | "DOCUMENTATION";
export type FirmwareTaskStatus = "PLANNED" | "IN_PROGRESS" | "COMPLETED" | "BLOCKED";

export type FirmwareModule = {
  module_id: string;
  firmware_spec_id: string;
  name: string;
  module_type: FirmwareModuleType;
  responsibility: string;
  dependencies: string[];
  hardware_bindings: string[];
  verification_refs: string[];
  source_refs: string[];
  fingerprint: string;
};

export type HardwareFirmwareBinding = {
  binding_id: string;
  hardware_interface_id: string;
  firmware_module_id: string;
  interface_role: FirmwareInterfaceRole;
  direction: string;
  signal_refs: string[];
  evidence_refs: string[];
  fingerprint: string;
};

export type FirmwareTask = {
  task_id: string;
  firmware_spec_id: string;
  module_id: string;
  title: string;
  task_type: FirmwareTaskType;
  dependencies: string[];
  expected_outputs: string[];
  verification_refs: string[];
  status: FirmwareTaskStatus;
  fingerprint: string;
};

export type FirmwareVerificationRequirement = {
  verification_id: string;
  target_module: string;
  method: string;
  acceptance_refs: string[];
  evidence_requirements: string[];
  simulation_refs: string[];
  fingerprint: string;
};

export type FirmwareSpecification = {
  firmware_spec_id: string;
  project_id: string;
  conversation_id: string;
  hardware_design_id: string;
  architecture_id: string;
  workflow_id: string;
  hardware_design_fingerprint: string;
  workflow_fingerprint: string;
  title: string;
  requirements: string[];
  runtime_constraints: string[];
  software_interfaces: string[];
  modules: FirmwareModule[];
  bindings: HardwareFirmwareBinding[];
  tasks: FirmwareTask[];
  verification_requirements: FirmwareVerificationRequirement[];
  source_refs: string[];
  provenance: unknown[];
  design_revision: string;
  fingerprint: string;
};

export type SimulationTargetType = "CONTROLLER" | "SENSOR" | "ACTUATOR" | "COMMUNICATION" | "POWER" | "FIRMWARE_MODULE" | "SYSTEM" | "UNKNOWN";
export type SimulationScenarioType = "FUNCTIONAL_TEST" | "INTERFACE_TEST" | "CONTROL_TEST" | "COMMUNICATION_TEST" | "PERFORMANCE_TEST" | "UNKNOWN";
export type SimulationEvidenceType = "LOG" | "TRACE" | "WAVEFORM" | "STATE_RESULT" | "MEASUREMENT" | "REPORT" | "UNKNOWN";
export type SimulationStatus = "PLANNED" | "REVIEWING" | "APPROVED" | "BLOCKED";
export type SimulationDesignIntent = "CONTROL_VALIDATION" | "INTERFACE_VALIDATION" | "FUNCTIONAL_VALIDATION" | "PERFORMANCE_VALIDATION" | "UNKNOWN";

export type SimulationModel = {
  model_id: string;
  simulation_spec_id: string;
  name: string;
  model_type: SimulationTargetType;
  source_reference: string;
  parameters: string[];
  interfaces: string[];
  evidence_refs: string[];
  fingerprint: string;
};

export type SimulationScenario = {
  scenario_id: string;
  simulation_spec_id: string;
  title: string;
  scenario_type: SimulationScenarioType;
  inputs: string[];
  expected_outputs: string[];
  related_models: string[];
  acceptance_refs: string[];
  fingerprint: string;
};

export type SimulationEvidenceRequirement = {
  evidence_id: string;
  simulation_spec_id: string;
  scenario_id: string;
  evidence_type: SimulationEvidenceType;
  description: string;
  acceptance_refs: string[];
  fingerprint: string;
};

export type SimulationSpecification = {
  simulation_spec_id: string;
  project_id: string;
  conversation_id: string;
  hardware_design_id: string;
  firmware_spec_id: string;
  workflow_id: string;
  hardware_design_fingerprint: string;
  firmware_spec_fingerprint: string;
  workflow_fingerprint: string;
  title: string;
  design_intent: SimulationDesignIntent;
  status: SimulationStatus;
  targets: string[];
  models: SimulationModel[];
  scenarios: SimulationScenario[];
  evidence_requirements: SimulationEvidenceRequirement[];
  verification_refs: string[];
  source_refs: string[];
  provenance: unknown[];
  design_revision: string;
  fingerprint: string;
};

export type VerificationStatus = "PLANNED" | "READY" | "RUNNING" | "PASSED" | "FAILED" | "BLOCKED";
export type VerificationCaseType = "FUNCTIONAL" | "INTERFACE" | "CONTROL" | "COMMUNICATION" | "PERFORMANCE" | "DESIGN_REVIEW";
export type VerificationEvidenceType = "LOG" | "TRACE" | "WAVEFORM" | "MEASUREMENT" | "SIMULATION_REPORT" | "TEST_REPORT" | "DOCUMENT" | "UNKNOWN";
export type VerificationIntent = "FUNCTIONAL_VALIDATION" | "INTERFACE_VALIDATION" | "CONTROL_VALIDATION" | "PERFORMANCE_VALIDATION" | "COMPLIANCE_VALIDATION";

export type VerificationCase = {
  case_id: string;
  verification_id: string;
  title: string;
  case_type: VerificationCaseType;
  verification_intent: VerificationIntent | null;
  target: string;
  related_requirement_refs: string[];
  related_design_refs: string[];
  method: string;
  expected_behavior: string[];
  evidence_refs: string[];
  status: VerificationStatus;
  fingerprint: string;
};

export type VerificationEvidenceRequirement = {
  evidence_id: string;
  verification_id: string;
  case_id: string;
  evidence_type: VerificationEvidenceType;
  description: string;
  source_reference: string;
  acceptance_refs: string[];
  fingerprint: string;
};

export type VerificationAcceptanceMapping = {
  mapping_id: string;
  verification_id: string;
  case_id: string;
  requirement_ref: string;
  acceptance_refs: string[];
  fingerprint: string;
};

export type VerificationSpecification = {
  schema_version: "1.0";
  verification_id: string;
  project_id: string;
  conversation_id: string;
  hardware_design_id: string;
  firmware_spec_id: string;
  simulation_spec_id: string;
  workflow_id: string;
  hardware_design_fingerprint: string;
  firmware_spec_fingerprint: string;
  simulation_spec_fingerprint: string;
  workflow_fingerprint: string;
  design_revision: string;
  verification_intent: VerificationIntent | null;
  cases: VerificationCase[];
  evidence_requirements: VerificationEvidenceRequirement[];
  acceptance_mapping: VerificationAcceptanceMapping[];
  status: VerificationStatus;
  provenance: unknown[];
  fingerprint: string;
};

export type ImplementationStatus = "PLANNED" | "READY" | "IN_PROGRESS" | "COMPLETED" | "BLOCKED" | "CANCELLED";
export type ImplementationTaskType = "HARDWARE_CONFIGURATION" | "FIRMWARE_MODULE" | "DRIVER_IMPLEMENTATION" | "INTERFACE_IMPLEMENTATION" | "CONTROL_ALGORITHM" | "COMMUNICATION" | "CONFIGURATION" | "TEST_PREPARATION" | "DOCUMENTATION";
export type ImplementationIntent = "MODULE_IMPLEMENTATION" | "INTERFACE_ADAPTATION" | "DRIVER_DEVELOPMENT" | "CONTROL_IMPLEMENTATION" | "CONFIGURATION_SETUP" | "TEST_PREPARATION" | "DOCUMENTATION";
export type ImplementationArtifactType = "SOURCE_CODE" | "CONFIGURATION" | "SCHEMATIC" | "PCB" | "DOCUMENT" | "TEST_ASSET" | "SIMULATION_ASSET" | "UNKNOWN";

export type ImplementationTask = {
  task_id: string;
  implementation_id: string;
  title: string;
  task_type: ImplementationTaskType;
  implementation_intent: ImplementationIntent;
  description: string;
  source_refs: string[];
  dependencies: string[];
  expected_artifacts: string[];
  verification_refs: string[];
  acceptance_refs: string[];
  status: ImplementationStatus;
  fingerprint: string;
};

export type ExpectedArtifact = {
  artifact_id: string;
  implementation_id: string;
  artifact_type: ImplementationArtifactType;
  name: string;
  description: string;
  source_refs: string[];
  verification_refs: string[];
  fingerprint: string;
};

export type ImplementationSpecification = {
  implementation_id: string;
  project_id: string;
  conversation_id: string;
  hardware_design_id: string;
  firmware_spec_id: string;
  simulation_spec_id: string;
  verification_id: string;
  workflow_id: string;
  hardware_design_fingerprint: string;
  firmware_spec_fingerprint: string;
  simulation_spec_fingerprint: string;
  verification_fingerprint: string;
  workflow_fingerprint: string;
  design_revision: string;
  tasks: ImplementationTask[];
  expected_artifacts: ExpectedArtifact[];
  dependencies: string[];
  verification_refs: string[];
  status: ImplementationStatus;
  provenance: unknown[];
  fingerprint: string;
};

export type CapabilityCategory = "IMPLEMENTATION" | "BUILD" | "SIMULATION" | "VERIFICATION" | "HARDWARE" | "FIRMWARE" | "ARTIFACT" | "SYSTEM";
export type CapabilityRiskLevel = "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
export type CapabilityStatus = "AVAILABLE" | "PLANNED" | "RESTRICTED" | "DISABLED";
export type ExecutionIntentType = "CREATE_ARTIFACT" | "GENERATE_SOURCE" | "BUILD_PROJECT" | "RUN_SIMULATION" | "RUN_VERIFICATION" | "FLASH_DEVICE" | "MODIFY_HARDWARE";
export type ExecutionIntentStatus = "PLANNED";
export type CapabilityApprovalStatus = "PENDING" | "APPROVED" | "REJECTED" | "EXPIRED";

export type EngineeringCapability = {
  capability_id: string;
  name: string;
  category: CapabilityCategory;
  description: string;
  risk_level: CapabilityRiskLevel;
  requires_approval: boolean;
  status: CapabilityStatus;
  source_refs: string[];
  fingerprint: string;
};

export type ImplementationCapabilityBinding = {
  binding_id: string;
  implementation_task_id: string;
  capability_id: string;
  required: boolean;
  reason: string;
  fingerprint: string;
};

export type ExecutionIntent = {
  intent_id: string;
  intent_type: ExecutionIntentType;
  source_task_refs: string[];
  required_capabilities: string[];
  status: ExecutionIntentStatus;
  fingerprint: string;
};

export type CapabilityApprovalRequirement = {
  approval_id: string;
  intent_id: string;
  required_capabilities: string[];
  risk_level: CapabilityRiskLevel;
  approval_status: CapabilityApprovalStatus;
  fingerprint: string;
};

export type CapabilityEvaluation = {
  evaluation_id: string;
  project_id: string;
  implementation_id: string;
  implementation_fingerprint: string;
  capabilities: EngineeringCapability[];
  bindings: ImplementationCapabilityBinding[];
  approval_requirements: CapabilityApprovalRequirement[];
  fingerprint: string;
};

export type ExecutionRequestStatus = "REQUESTED" | "AUTHORIZED" | "DENIED" | "EXPIRED";
export type AuthorizationDecisionStatus = "PENDING" | "APPROVED" | "REJECTED";
export type ExecutionAuthorizationStatus = "PENDING" | "AUTHORIZED" | "BLOCKED";

export type ExecutionRequest = {
  request_id: string;
  intent_id: string;
  capability_id: string;
  project_id: string;
  conversation_id: string;
  requested_by: string;
  request_reason: string;
  risk_level: CapabilityRiskLevel;
  status: ExecutionRequestStatus;
  source_refs: string[];
  fingerprint: string;
};

export type ExecutionPolicy = {
  policy_id: string;
  capability_id: string;
  risk_level: CapabilityRiskLevel;
  requires_user_confirmation: boolean;
  requires_review: boolean;
  allowed: boolean;
  fingerprint: string;
};

export type AuthorizationDecision = {
  decision_id: string;
  request_id: string;
  decision: AuthorizationDecisionStatus;
  reason: string;
  approved_by: string | null;
  timestamp_reference: string | null;
  fingerprint: string;
};

export type ExecutionAuthorization = {
  authorization_id: string;
  project_id: string;
  conversation_id: string;
  capability_evaluation_id: string;
  requests: ExecutionRequest[];
  policies: ExecutionPolicy[];
  decisions: AuthorizationDecision[];
  status: ExecutionAuthorizationStatus;
  fingerprint: string;
};

export type FirmwareSourceProposalStatus = "AWAITING_APPROVAL" | "APPROVED" | "REJECTED";
export type FirmwareSourceGenerationStatus = "AWAITING_APPROVAL" | "QUEUED" | "REJECTED";

export type FirmwareSourceGenerationRequest = {
  request_id: string;
  project_id: string;
  conversation_id: string;
  requester: string;
  implementation_task_id: string;
  firmware_module_id: string;
  authorization_request_id: string;
  verification_refs: string[];
  fingerprint: string;
};

export type FirmwareSourceFileProposal = {
  relative_path: string;
  content_fingerprint: string;
  size_bytes: number;
};

export type FirmwareSourceProposal = {
  proposal_id: string;
  firmware_module_id: string;
  module_name: string;
  files: FirmwareSourceFileProposal[];
  verification_refs: string[];
  status: FirmwareSourceProposalStatus;
  firmware_evidence_parse: "UNAVAILABLE";
  fingerprint: string;
};

export type FirmwareSourceProposalDecision = {
  decision: "PENDING" | "APPROVED" | "REJECTED";
};

export type FirmwareSourceGenerationState = {
  request: FirmwareSourceGenerationRequest;
  proposal: FirmwareSourceProposal;
  decision: FirmwareSourceProposalDecision;
  operation_id: string | null;
  status: FirmwareSourceGenerationStatus;
  fingerprint: string;
};

export type SchematicProductionStatus = "AWAITING_APPROVAL" | "QUEUED" | "REJECTED" | "GENERATED" | "FAILED" | "STALE";
export type SchematicRepairDecisionAction = "APPROVE_FIX" | "REJECT_FIX" | "EDIT_FIX" | "DEFER" | "WAIVE_WITH_REASON";
export type SchematicRepairProposal = {
  repair_id: string;
  erc_result_fingerprint: string;
  corrected_content_fingerprint: string;
  target_relative_path: string;
  status: "AWAITING_APPROVAL";
  issue_refs: string[];
  classification: string;
  root_cause: string;
  proposed_change: string;
  expected_effect: string;
  risk: string;
  source_erc_receipt_fingerprint?: string | null;
  fingerprint: string;
};
export type SchematicRepairDecision = {
  decision_id: string;
  repair_id: string;
  proposal_fingerprint: string;
  artifact_fingerprint: string;
  action: SchematicRepairDecisionAction;
  reviewer: string;
  decided_at: string;
  comment: string;
  edited_proposal_fingerprint: string | null;
  fingerprint: string;
};
export type SchematicRepairApplicationReceipt = {
  receipt_id: string;
  project_id: string;
  conversation_id: string;
  repair_id: string;
  repair_proposal_fingerprint: string;
  repair_decision_id: string;
  repair_decision_fingerprint: string;
  repair_action: "APPROVE_FIX" | "EDIT_FIX";
  artifact_path: string;
  pre_design_truth_fingerprint: string;
  post_design_truth_fingerprint: string;
  pre_schematic_plan_fingerprint: string;
  post_schematic_plan_fingerprint: string;
  pre_artifact_fingerprint: string;
  post_artifact_fingerprint: string;
  pre_erc_receipt_fingerprint: string;
  pre_erc_result_fingerprint: string;
  workspace_apply_receipt_fingerprint: string;
  post_generation_receipt_fingerprint: string;
  post_parse_validation_fingerprint: string;
  post_erc_receipt_fingerprint: string;
  post_erc_result_fingerprint: string;
  status: "APPLIED" | "STALE";
  design_truth_changed: boolean;
  schematic_plan_changed: boolean;
  generator_changed: boolean;
  applied_at: string;
  fingerprint: string;
};
export type SchematicProductionState = {
  request: { request_id: string; project_id: string; conversation_id: string; requester: string; target_relative_path: string; fingerprint: string };
  proposal: { proposal_id: string; components: { reference: string; value: string; symbol_binding: string }[]; nets: { net_name: string; nodes: string[] }[]; status: "AWAITING_APPROVAL"; fingerprint: string };
  decision: { decision: "PENDING" | "APPROVED" | "REJECTED"; reviewer?: string | null; decided_at?: string | null };
  operation_id: string | null;
  generation_receipt?: { operation_id?: string; operation_receipt_fingerprint?: string; artifact_path: string; artifact_fingerprint: string; schematic_plan_fingerprint: string; design_truth_fingerprint: string; validation_status: string; parse_validation?: { fingerprint: string } | null; fingerprint: string } | null;
  erc_receipt?: { operation_id: string; artifact_fingerprint: string; result: { status: string; engineering_status: string; issues: Array<{ issue_id: string; severity: string; rule_id: string; classification: string; message: string; component_ref?: string | null; pin_ref?: string | null; location?: string | null; raw_evidence_ref: string }> }; fingerprint: string } | null;
  erc_assessment?: { kind: string; classification: string; proposal_allowed: boolean; rationale: string } | null;
  repair_proposals?: SchematicRepairProposal[];
  repair_decisions?: SchematicRepairDecision[];
  repair_application_receipts?: SchematicRepairApplicationReceipt[];
  erc_gate_status?: "PASS" | "BLOCKED" | "PARTIAL" | null;
  status: SchematicProductionStatus;
  fingerprint: string;
};

export type ConversationDetail = {
  conversation_id: string;
  project_id: string;
  tenant_id?: string;
  requester?: string;
  title: string;
  turns: ConversationTurn[];
  created_at: string;
  updated_at: string;
  requirement_context?: RequirementContext | null;
  research_snapshot?: EngineeringResearchSnapshot | null;
  reference_project_decisions?: ReferenceProjectDecision[];
  feature_decisions?: FeatureDecision[];
  requirement_change_proposal?: RequirementChangeProposal | null;
  requirement_final_confirmation?: RequirementFinalConfirmation | null;
  research_intent_refinement?: ResearchIntentRefinement | null;
  engineering_plan?: EngineeringPlan | null;
  engineering_plan_review?: EngineeringPlanReview | null;
  architecture_design?: ArchitectureDesign | null;
  architecture_review?: ArchitectureReviewDecision | null;
  selection_head?: SelectionRevisionHead;
  hardware_selection?: {
    status: string;
    proposal?: { proposal_id: string; fingerprint: string; selected_components: Array<{ slot_id: string; source: { source_id: string; fingerprint: string; manufacturer: string; mpn: string; package: string } }> };
    approval: { approval_id: string; fingerprint: string } | null;
    bom: { fingerprint: string } | null;
    hardware_truth: { fingerprint: string } | null;
  } | null;
  design_truth?: DesignTruth | null;
  engineering_knowledge_model?: EngineeringKnowledgeModel | null;
  teaching_context?: TeachingContext | null;
  engineering_workflow?: EngineeringWorkflow | null;
  hardware_design_spec?: HardwareDesignSpecification | null;
  firmware_spec?: FirmwareSpecification | null;
  simulation_spec?: SimulationSpecification | null;
  verification_spec?: VerificationSpecification | null;
  implementation_spec?: ImplementationSpecification | null;
  capability_evaluation?: CapabilityEvaluation | null;
  execution_intents?: ExecutionIntent[];
  execution_authorization?: ExecutionAuthorization | null;
  firmware_source_generation?: FirmwareSourceGenerationState | null;
  schematic_planning?: { status: string; proposal?: SchematicPlanView; approved_plan?: SchematicPlanView | null } | null;
  schematic_production?: SchematicProductionState | null;
  artifact_intelligence_snapshot?: ArtifactIntelligenceView | null;
  fingerprint: string;
};

export type ResearchResultClassification = "DIRECT_MATCH" | "ANALOGOUS_MATCH" | "SUBSYSTEM_MATCH" | "OFFICIAL_REFERENCE_ONLY" | "NO_RELEVANT_REFERENCE";
export type ResearchStatus = "COMPLETE" | "PARTIAL" | "NO_RELEVANT_REFERENCE" | "UNAVAILABLE";
export type FeatureDeltaKind = "MATCHED" | "REFERENCE_EXTRA" | "USER_ONLY" | "DIFFERENT" | "UNSPECIFIED";
export type FeatureEvidenceState = "PRESENT" | "INFERRED";
export type ReferenceProjectDecisionAction = "REFERENCE" | "PARTIAL_REFERENCE" | "EXCLUDE" | "DEFER";
export type FeatureDecisionAction = "ADD" | "REMOVE" | "MODIFY" | "KEEP" | "REJECT" | "DEFER";
export type RequirementChangeAction = "ADD" | "REMOVE" | "MODIFY" | "KEEP" | "REJECT" | "DEFER";

export type ReferenceFeatureDelta = {
  delta_id: string;
  feature: string;
  delta: FeatureDeltaKind;
  evidence_state: FeatureEvidenceState;
  evidence_refs: string[];
  description: string;
  fingerprint: string;
};
export type ReferenceProjectReview = {
  project_identity: string;
  title: string;
  description: string;
  decision: ReferenceProjectDecisionAction;
  selected_features: string[];
  feature_deltas: ReferenceFeatureDelta[];
  fingerprint: string;
};
export type ReferenceProjectDecision = {
  decision_id: string;
  project_identity: string;
  decision: ReferenceProjectDecisionAction;
  selected_features: string[];
  fingerprint: string;
};
export type FeatureDecision = {
  decision_id: string;
  project_identity: string;
  feature: string;
  action: FeatureDecisionAction;
  value: string | null;
  fingerprint: string;
};
export type RequirementDecision = {
  decision_id: string;
  action: RequirementChangeAction;
  target: string;
  category: string;
  value: string | null;
  current_requirement_id: string | null;
  evidence_refs: string[];
  source: string;
  fingerprint: string;
};
export type RequirementChangeProposal = {
  proposal_id: string;
  base_requirement_fingerprint: string;
  research_fingerprint: string | null;
  decisions: RequirementDecision[];
  summary: string;
  status: "PENDING" | "CONFIRMED" | "REJECTED" | "STALE";
  fingerprint: string;
};
export type RequirementFinalConfirmation = {
  confirmation_id: string;
  proposal_id: string;
  proposal_fingerprint: string;
  confirmed: boolean;
  fingerprint: string;
};
export type ResearchIntentRefinement = {
  refinement_id: string;
  product_positioning: string;
  negative_constraints: string[];
  fingerprint: string;
};
export type EngineeringResearchSnapshot = {
  classification: ResearchResultClassification;
  evidence_pack: {
    status: ResearchStatus;
    projects: { project_identity: string; title: string; url: string; classification: ResearchResultClassification; fingerprint: string }[];
    source_reports: { source_type: string; status: string; candidate_count: number; error_code: string | null }[];
    fingerprint: string;
  };
  reference_reviews: ReferenceProjectReview[];
  feature_decisions: FeatureDecision[];
  fingerprint: string;
};

export type ArtifactIntelligenceView = {
  files: { name: string; type: string; status: string }[];
  coverage: {
    schematic: string;
    pcb: string;
    bom: string;
    firmware: string;
    gerber: string;
    documentation: string;
    completeness_percent: number;
  };
  available_evidence: string[];
  evidence_review_status: EngineeringEvidenceReviewStatus;
  missing_evidence: string[];
};

export type DesignTruth = {
  truth_id: string;
  project_id: string;
  design_revision: string;
  conversation_id?: string;
  architecture_id?: string | null;
  origin?: "CREATE" | "REVERSE";
  status?: "APPROVED";
  components: DesignComponent[];
  signals: DesignSignal[];
  pins: DesignPin[];
  peripherals: PeripheralBinding[];
  nets: NetBinding[];
  interfaces: DesignInterface[];
  power_domains: DesignPowerDomain[];
  firmware_bindings: FirmwareBinding[];
  evidence_bindings: DesignEvidenceBinding[];
  provenance: unknown[];
  fingerprint: string;
};

export type EngineeringKnowledgeEntity = {
  entity_id: string;
  knowledge_id: string;
  entity_type: EngineeringKnowledgeEntityType;
  name: string;
  description: string;
  responsibility: string;
  truth_level: TruthLevel;
  confidence: KnowledgeConfidence;
  source_truth_refs: string[];
  evidence_bindings: string[];
  fingerprint: string;
};

export type EngineeringKnowledgeRelationship = {
  relationship_id: string;
  knowledge_id: string;
  source_entity_id: string;
  target_entity_id: string;
  relationship_type: EngineeringKnowledgeRelationshipType;
  description: string;
  evidence_bindings: string[];
  fingerprint: string;
};

export type EngineeringLearningTopic = {
  topic_id: string;
  title: string;
  description: string;
  related_entities: string[];
  difficulty: string;
  source_refs: string[];
  evidence_bindings: string[];
  fingerprint: string;
};

export type EngineeringKnowledgeModel = {
  schema_version: "1.0";
  knowledge_id: string;
  project_id: string;
  conversation_id: string;
  source_truth_fingerprint: string;
  title: string;
  summary: string;
  system_context: string[];
  subsystems: string[];
  entities: EngineeringKnowledgeEntity[];
  relationships: EngineeringKnowledgeRelationship[];
  learning_topics: EngineeringLearningTopic[];
  modification_points: string[];
  design_decisions: string[];
  risks: string[];
  evidence_bindings: string[];
  provenance: unknown[];
  fingerprint: string;
};

export type TeachingSubject = {
  subject_id: string;
  entity_id: string;
  entity_type: TeachingSubjectEntityType;
  title: string;
  summary: string;
  related_entities: string[];
  source_refs: string[];
  evidence_bindings: string[];
  confidence: KnowledgeConfidence;
  fingerprint: string;
};

export type TeachingLearningStep = {
  step_id: string;
  step_type: TeachingStepType;
  title: string;
  step_summary: string;
  related_entities: string[];
  order: number;
  evidence_refs: string[];
  fingerprint: string;
};

export type TeachingLearningPath = {
  path_id: string;
  teaching_context_id: string;
  steps: TeachingLearningStep[];
  fingerprint: string;
};

export type TeachingContext = {
  schema_version: "1.0";
  teaching_context_id: string;
  project_id: string;
  conversation_id: string;
  knowledge_id: string;
  subject: TeachingSubject;
  scope: TeachingScope;
  difficulty: TeachingDifficulty;
  learning_mode: LearningMode;
  related_entities: string[];
  learning_path: TeachingLearningPath;
  evidence_bindings: string[];
  source_knowledge_schema_version: "1.0";
  source_knowledge_fingerprint: string;
  fingerprint: string;
};

export type DesignEvidenceBinding = {
  binding_id: string;
  entity_id: string;
  project_id: string;
  source: DesignSource;
  source_id: string;
  artifact_ref: string;
  locator?: string | null;
  artifact_revision?: string | null;
  source_fingerprint: string;
  relationship: string;
  truth_level: TruthLevel;
};

export type DesignComponent = {
  component_id: string;
  name: string;
  type: string;
  category: string;
  description: string;
  confidence: number;
  truth_level: TruthLevel;
  evidence_refs: string[];
};

export type DesignPin = {
  pin_id: string;
  component_id: string;
  pin_name: string;
  pin_number: string;
  function: string;
  signal_id?: string | null;
  confidence: number;
  truth_level: TruthLevel;
  evidence_refs: string[];
};

export type DesignSignal = {
  signal_id: string;
  name: string;
  type: string;
  source: string;
  targets: string[];
  protocol: string;
  direction: string;
  confidence: number;
  truth_level: TruthLevel;
  evidence_refs: string[];
};

export type PeripheralBinding = {
  binding_id: string;
  peripheral: string;
  component: string;
  pin_refs: string[];
  function: string;
  confidence: number;
  truth_level: TruthLevel;
  evidence_refs: string[];
};

export type NetBinding = {
  binding_id: string;
  net_name: string;
  nodes: string[];
  signal: string;
  purpose: string;
  confidence: number;
  truth_level: TruthLevel;
  evidence_refs: string[];
};

export type DesignInterface = {
  interface_id: string;
  source: string;
  target: string;
  protocol: string;
  direction: string;
  confidence: number;
  truth_level: TruthLevel;
  evidence_refs: string[];
};

export type DesignPowerDomain = {
  power_domain_id: string;
  name: string;
  component_refs: string[];
  purpose: string;
  confidence: number;
  truth_level: TruthLevel;
  evidence_refs: string[];
};

export type FirmwareBinding = {
  binding_id: string;
  firmware_symbol: string;
  source_file: string;
  hardware_refs: string[];
  function: string;
  truth_level: TruthLevel;
  evidence_refs: string[];
};

export type EngineeringPlan = {
  plan_id: string;
  project_id: string;
  requirement_id: string;
  title: string;
  objective: string;
  status: string;
  phases: EngineeringPlanPhase[];
  model_operation_bindings?: Array<{
    phase_id: string; plan_task_id: string; operation_id: string; stage_id: string;
  }>;
  verification_strategy: EngineeringPlanVerification[];
  fingerprint: string;
};

export type EngineeringPlanPhase = {
  phase_id: string;
  name: string;
  description: string;
  status: string;
  tasks: Array<{ task_id: string; title: string; status: string }>;
  outputs: Array<{ output_id: string; name: string; output_type: string; status: string }>;
  verification: EngineeringPlanVerification[];
};

export type EngineeringPlanVerification = {
  strategy_id: string;
  method: string;
  criterion_ids: string[];
  evidence_required: string[];
};

export type ArchitectureDesign = {
  architecture_id: string;
  project_id: string;
  plan_id: string;
  title: string;
  objective: string;
  status: string;
  system_components: ArchitectureComponent[];
  interfaces: ArchitectureInterface[];
  boundary: ArchitectureBoundary;
  data_flow: ArchitectureFlow[];
  control_flow: ArchitectureFlow[];
  decisions: ArchitectureDecision[];
  verification: ArchitectureVerification[];
  constraints: string[];
  context_fingerprint: string;
  plan_fingerprint: string;
  provenance?: unknown[];
  fingerprint: string;
};

export type ArchitectureComponent = {
  component_id: string;
  name: string;
  category: string;
  responsibility: string;
  level: string;
  confidence: string;
  evidence_binding_ids?: string[];
};

export type ArchitectureInterface = {
  interface_id: string;
  source_component_id: string;
  target_component_id: string;
  interface_type: string;
  protocol: string;
  verification: string;
  confidence: string;
};

export type ArchitectureBoundary = {
  hardware_component_ids: string[];
  software_component_ids: string[];
};

export type ArchitectureFlow = {
  flow_id: string;
  flow_type: string;
  source_component_id: string;
  target_component_id: string;
  data_or_command: string;
  confidence: string;
};

export type ArchitectureDecision = {
  decision_id: string;
  subject: string;
  selected_option: string;
  alternatives: string[];
  decision_type: string;
  rationale: string;
  confidence: string;
};

export type ArchitectureVerification = {
  verification_id: string;
  method: string;
  evidence_required: string[];
  acceptance_criterion_ids: string[];
};

export type ArchitectureReviewDecision = {
  architecture_id: string;
  plan_id: string;
  project_id: string;
  proposal_fingerprint: string;
  status: string;
  reviewer: string;
  decided_at: string;
  findings?: ArchitectureReviewFinding[];
  fingerprint: string;
};

export type EngineeringWorkflowVerification = {
  verification_id: string;
  task_id: string;
  method: string;
  acceptance_refs: string[];
  evidence_refs: string[];
  status: EngineeringWorkflowItemStatus;
};

export type EngineeringWorkflowArtifact = {
  artifact_id: string;
  task_id: string;
  artifact_type: EngineeringWorkflowArtifactType;
  name: string;
  status: EngineeringWorkflowItemStatus;
  source_refs: string[];
};

export type EngineeringWorkflowTask = {
  task_id: string;
  phase_id: string;
  title: string;
  description: string;
  task_type: EngineeringWorkflowTaskType;
  status: EngineeringWorkflowItemStatus;
  dependencies: string[];
  input_refs: string[];
  output_refs: string[];
  verification_refs: string[];
  expected_outputs: string[];
  verification_requirements: string[];
};

export type EngineeringWorkflowPhase = {
  phase_id: string;
  workflow_id: string;
  phase_type: EngineeringWorkflowPhaseType;
  name: string;
  description: string;
  order: number;
  status: EngineeringWorkflowItemStatus;
  tasks: EngineeringWorkflowTask[];
  outputs: EngineeringWorkflowArtifact[];
  verification_refs: EngineeringWorkflowVerification[];
  dependencies: string[];
};

export type EngineeringWorkflow = {
  workflow_id: string;
  project_id: string;
  conversation_id: string;
  plan_id: string;
  architecture_id: string;
  title: string;
  status: EngineeringWorkflowStatus;
  phases: EngineeringWorkflowPhase[];
  current_phase_id: string | null;
  workflow_version: "1.0";
  provenance: unknown[];
  fingerprint: string;
};

export type ArchitectureReviewFinding = {
  finding_id: string;
  category: string;
  description: string;
  status: string;
};

export type EngineeringPlanReview = {
  plan_id: string;
  project_id: string;
  status: string;
  reviewer: string;
  decided_at: string;
};

export type RequirementContext = {
  project_id: string;
  conversation_id: string;
  standard_fields: Array<{ category: string; value: string | null; status: string }>;
  requirements: Array<{ requirement_id: string; category: string; normalized_value: string; status: string; provenance: string; priority?: string }>;
  constraints: Array<{ requirement_id: string; category: string; normalized_value: string; status: string; provenance: string; priority?: string }>;
  proposed_items: Array<{ requirement_id: string; category: string; normalized_value: string; status: string; provenance: string; priority?: string }>;
  open_questions: Array<{ category: string; question: string; provenance: string }>;
  acceptance_criteria?: Array<{ given: string; when: string; then: string; verification: string; status: string }>;
  conflicts?: Array<{ description: string; severity: string; status: string }>;
  status?: "DRAFT" | "ANALYZING" | "CLARIFICATION_REQUIRED" | "CONFIRMED" | "ARCHIVED" | string;
  lifecycle: "DISCOVERY_STARTED" | "REQUIREMENTS_PARTIAL" | "REQUIREMENTS_REFINING" | "REQUIREMENTS_READY" | "DESIGN_READY";
  fingerprint: string;
};

export type ConversationSummary = {
  conversation_id: string;
  project_id: string;
  title: string;
  turn_count: number;
  created_at: string;
  updated_at: string;
  fingerprint: string;
};

export type ConversationSession = {
  conversation: ConversationDetail;
  session_id: string;
  chat_available: boolean;
};

const headers = {
  "Content-Type": "application/json",
  "X-Copilot-Requester": "desktop-user",
  "X-Environment-Tenant": "desktop-tenant",
};

export class ProjectCatalogRequestError extends Error {
  constructor(readonly code: string, readonly blockingReason: string | null = null) {
    super(code);
    this.name = "ProjectCatalogRequestError";
  }
}

export function projectLocationErrorMessage(value: unknown): string {
  const code = value instanceof ProjectCatalogRequestError ? value.code : "";
  switch (code) {
    case "INVALID_DIRECTORY": return "请选择有效的本地文件夹。";
    case "PERMISSION_DENIED": return "当前目录权限不足，请选择其他位置。";
    case "UNSAFE_LOCATION": return "该位置不能作为工程目录。";
    case "PROJECT_CATALOG_CONFLICT": return "已添加该工程位置。";
    default: return "工程位置不可用，请检查路径、权限和受控目录范围。";
  }
}

function reportProjectLocationsDiagnostic(
  sink: FrontendProviderDiagnosticSink | undefined,
  event: Omit<FrontendProviderDiagnosticEvent, "route">,
): void {
  if (sink === undefined) return;
  try {
    const delivery = sink({ ...event, route: "PROJECT_LOCATIONS" });
    if (delivery !== undefined) void Promise.resolve(delivery).catch(() => undefined);
  } catch {
    // Project Locations diagnostics are observational and must not change catalog behavior.
  }
}

function diagnosticClock(): number {
  return typeof globalThis.performance?.now === "function" ? globalThis.performance.now() : Date.now();
}

async function call<T>(
  path: string,
  init: RequestInit = {},
  fetcher: typeof fetch = fetch,
  diagnosticSink?: FrontendProviderDiagnosticSink,
): Promise<T> {
  if (isDesktopSessionExpired()) throw new ProjectCatalogRequestError(DESKTOP_SESSION_EXPIRED);
  const method = String(init.method ?? "GET").toUpperCase();
  const observeLocations = path === "/locations" && method === "GET";
  const projectLocationsAttemptStartedAt = observeLocations ? new Date().toISOString() : undefined;
  const projectLocationsAttemptStartedClock = observeLocations ? diagnosticClock() : undefined;
  const terminalProjectLocationsEvidence = (
    frontendFetchResolved: boolean,
    frontendHttpStatus: number | "UNKNOWN",
    finalResult: "SUCCESS" | "FAILURE",
  ) => observeLocations ? {
    project_locations_attempt_started_at: projectLocationsAttemptStartedAt,
    project_locations_attempt_finished_at: new Date().toISOString(),
    project_locations_attempt_duration_ms: Math.max(0, Math.round(diagnosticClock() - (projectLocationsAttemptStartedClock ?? diagnosticClock()))),
    frontend_fetch_resolved: frontendFetchResolved,
    frontend_http_status: frontendHttpStatus,
    final_result: finalResult,
  } : {};
  if (observeLocations) {
    reportProjectLocationsDiagnostic(diagnosticSink, {
      stage: "WEBVIEW_FRONTEND_PROJECT_LOCATIONS_FETCH_BEGIN",
      result: "ENTERED",
    });
  }
  let response: Response;
  try {
    response = await fetcher(`/api/v1/copilot/projects${path}`, { credentials: "same-origin", ...init, headers: { ...headers, ...(method === "GET" ? webViewDiagnosticOriginHeaders() : {}), ...(init.headers ?? {}) } });
  } catch (error: unknown) {
    if (observeLocations) {
      reportProjectLocationsDiagnostic(diagnosticSink, {
        stage: "WEBVIEW_FRONTEND_PROJECT_LOCATIONS_FETCH_FAILURE",
        result: "FAILURE",
        ...terminalProjectLocationsEvidence(false, "UNKNOWN", "FAILURE"),
        safe_error_class: "FETCH_REJECTED",
        failure_stage: "FETCH",
        fetch_failure_kind: "PROMISE_REJECTION",
        fetch_failure_stage: "FETCH_CALL",
      });
    }
    throw error;
  }
  if (observeLocations) {
    reportProjectLocationsDiagnostic(diagnosticSink, {
      stage: "WEBVIEW_FRONTEND_PROJECT_LOCATIONS_FETCH_PROMISE_RESOLVED",
      result: "RECEIVED",
      http_status: response.status,
    });
  }
  if (!response.ok) {
    if (observeLocations) {
      reportProjectLocationsDiagnostic(diagnosticSink, {
        stage: "WEBVIEW_FRONTEND_PROJECT_LOCATIONS_FETCH_FAILURE",
        result: "FAILURE",
        ...terminalProjectLocationsEvidence(true, response.status, "FAILURE"),
        http_status: response.status,
        safe_error_class: "HTTP_STATUS_ERROR",
        failure_stage: "HTTP_STATUS",
        fetch_failure_kind: "HTTP_STATUS",
        fetch_failure_stage: "HTTP_STATUS",
      });
    }
    const payload = await response.json().catch(() => ({}));
    if (response.status === 409 && payload?.status === "BLOCKED"
        && payload?.operation_id === "SCHEMATIC_DESIGN_PROPOSAL"
        && Array.isArray(payload?.blocking_reasons) && payload.blocking_reasons.length === 1) {
      const reason = payload.blocking_reasons[0];
      if (["POWER_SOURCE_UNRESOLVED", "POWER_NET_INCOMPLETE", "INTERFACE_PROTOCOL_UNRESOLVED", "PIN_BINDING_UNRESOLVED"].includes(reason)
          && payload.reason_code === reason) {
        throw new ProjectCatalogRequestError(reason, reason);
      }
    }
    const code = mapDesktopSessionError(response.status, readDesktopSessionErrorCode(payload)) ?? "PROJECT_CATALOG_UNAVAILABLE";
    throw new ProjectCatalogRequestError(code);
  }
  try {
    const payload = await response.json() as T;
    if (observeLocations) {
      reportProjectLocationsDiagnostic(diagnosticSink, {
        stage: "WEBVIEW_FRONTEND_PROJECT_LOCATIONS_FETCH_SUCCESS",
        result: "SUCCESS",
        ...terminalProjectLocationsEvidence(true, response.status, "SUCCESS"),
        http_status: response.status,
      });
    }
    return payload;
  } catch (error: unknown) {
    if (observeLocations) {
      reportProjectLocationsDiagnostic(diagnosticSink, {
        stage: "WEBVIEW_FRONTEND_PROJECT_LOCATIONS_FETCH_FAILURE",
        result: "FAILURE",
        ...terminalProjectLocationsEvidence(true, response.status, "FAILURE"),
        http_status: response.status,
        safe_error_class: "JSON_PARSE_ERROR",
        failure_stage: "JSON_PARSE",
        fetch_failure_kind: "RESPONSE_BODY",
        fetch_failure_stage: "RESPONSE_BODY",
      });
    }
    throw error;
  }
}

export function requestSchematicModelCandidate(
  projectId: string, conversationId: string,
  input: { request_id: string; plan_id: string; phase_id: string; plan_task_id: string },
  fetcher: typeof fetch = fetch,
): Promise<{ status: string; operation_id: string }> {
  return call(`/${encodeURIComponent(projectId)}/conversations/${encodeURIComponent(conversationId)}/schematic-plan/model-candidate-proposal`,
    { method: "POST", body: JSON.stringify(input) }, fetcher);
}

export function requestHardwareModelCandidate(
  projectId: string, conversationId: string,
  input: { request_id: string; plan_id: string; phase_id: string; plan_task_id: string },
  fetcher: typeof fetch = fetch,
): Promise<{ status: string; operation_id: string }> {
  return call(`/${encodeURIComponent(projectId)}/conversations/${encodeURIComponent(conversationId)}/hardware-selection/model-candidate-proposal`,
    { method: "POST", body: JSON.stringify(input) }, fetcher);
}

export type SchematicEvidenceOperation = {
  operation_id: string; operation_type: "KICAD_SCHEMATIC_PARSE" | "KICAD_SCHEMATIC_READ_BACK";
  tenant_id: string; project_id: string; conversation_id: string; requester_id: string;
  expires_at: string; fingerprint: string;
  library_map_evidence_id?: string; library_map_evidence_fingerprint?: string;
};
export const createSchematicEvidenceOperation = (
  projectId: string, conversationId: string, kind: "PARSE" | "READBACK", fetcher: typeof fetch = fetch,
) => call<SchematicEvidenceOperation>(`/${encodeURIComponent(projectId)}/conversations/${encodeURIComponent(conversationId)}/schematic-evidence/operations`,
  { method: "POST", body: JSON.stringify({ kind }) }, fetcher);

export type SchematicParameterCandidate = {
  evidence_id: string; tenant_id: string; project_id: string; conversation_id: string;
  source_record_id: string; source_record_fingerprint: string; manufacturer: string; mpn: string; package: string;
  document_uri: string; document_revision: string; content_sha256: string;
  locator: { page?: number | null; table?: string | null; row?: string | null; section?: string | null; clause?: string | null };
  parameter_key: "SUPPLY_MIN" | "SUPPLY_MAX" | "PULLUP_RESISTANCE" | "PULLUP_TOLERANCE" | "BUS_CAPACITANCE_MAX" | "RISE_TIME_MAX" | "BUS_FREQUENCY_MAX" | "DECOUPLING_NOMINAL" | "DECOUPLING_EFFECTIVE_MIN" | "VOL_MAX" | "VIL_MAX" | "IOL_MIN" | "LEAKAGE_MAX";
  bound_kind: "MIN" | "MAX" | "TYP" | "EXACT"; value_decimal: string;
  unit: "V" | "ohm" | "F" | "s" | "Hz" | "ratio" | "A";
  conditions: { voltage_min: string | null; voltage_max: string | null; temperature_min: string | null; temperature_max: string | null; mode: "DC" | "I2C_STANDARD" | "UNKNOWN"; package: string | null };
  fingerprint?: string;
};
export type SchematicParameterView = {
  evidence: { candidate: SchematicParameterCandidate & { fingerprint: string }; review: { decision: "APPROVED"; reviewer: string; fingerprint: string } };
  currentness: "CURRENT" | "STALE";
};
export const proposeSchematicParameter = (
  projectId: string, conversationId: string, candidate: SchematicParameterCandidate, fetcher: typeof fetch = fetch,
) => call<SchematicParameterCandidate & { fingerprint: string }>(`/${encodeURIComponent(projectId)}/conversations/${encodeURIComponent(conversationId)}/schematic-parameters`,
  { method: "POST", body: JSON.stringify(candidate) }, fetcher);
export const readSchematicParameter = (projectId: string, evidenceId: string, fetcher: typeof fetch = fetch) =>
  call<SchematicParameterView>(`/${encodeURIComponent(projectId)}/schematic-parameters/${encodeURIComponent(evidenceId)}`, {}, fetcher);
export const reviewSchematicParameter = (
  projectId: string, evidenceId: string, evidenceFingerprint: string, decision: "APPROVED" | "REJECTED", fetcher: typeof fetch = fetch,
) => call<{ decision: "APPROVED" | "REJECTED"; fingerprint: string }>(`/${encodeURIComponent(projectId)}/schematic-parameters/${encodeURIComponent(evidenceId)}/review`,
  { method: "POST", body: JSON.stringify({ evidence_fingerprint: evidenceFingerprint, decision }) }, fetcher);

export const fetchCatalogProjects = (fetcher: typeof fetch = fetch) => call<ProjectView[]>("", {}, fetcher);
export const fetchProjectLocations = (
  fetcher: typeof fetch = fetch,
  diagnosticSink: FrontendProviderDiagnosticSink = recordFrontendProviderDiagnostic,
) => call<ProjectLocation[]>("/locations", {}, fetcher, diagnosticSink);
export function registerProjectLocation(path: string, displayLabel?: string, fetcher: typeof fetch = fetch): Promise<ProjectLocation> {
  return call("/locations", { method: "POST", body: JSON.stringify({ path, display_label: displayLabel || null }) }, fetcher);
}
export const fetchProjectDirectories = (locationId: string, fetcher: typeof fetch = fetch) => call<{ items: ProjectDirectory[] }>(`/locations/${encodeURIComponent(locationId)}/directories`, {}, fetcher);
export const fetchCatalogProject = (projectId: string, fetcher: typeof fetch = fetch) => call<ProjectView>(`/${encodeURIComponent(projectId)}`, {}, fetcher);
export const previewEdaImport = (projectId: string, input: EdaImportPreviewInput, fetcher: typeof fetch = fetch) =>
  call<EdaImportPreview>(`/${encodeURIComponent(projectId)}/eda-import-previews`, { method: "POST", body: JSON.stringify(input) }, fetcher);
export const commitEdaImport = (projectId: string, input: { preview_id: string; preview_fingerprint: string }, fetcher: typeof fetch = fetch) =>
  call<EdaImportCommit>(`/${encodeURIComponent(projectId)}/eda-imports`, { method: "POST", body: JSON.stringify(input) }, fetcher);
export const listEdaImportRevisions = (projectId: string, backend: EdaImportBackend, nativeEntryRelativePath: string, fetcher: typeof fetch = fetch, cursor?: string) => {
  const query = new URLSearchParams({ backend, native_entry_relative_path: nativeEntryRelativePath });
  if (cursor) query.set("cursor", cursor);
  return call<{ items: EdaImportRevisionSummary[]; next_cursor: string | null }>(`/${encodeURIComponent(projectId)}/eda-imports?${query}`, {}, fetcher);
};
export const fetchEdaImportRevision = (projectId: string, revisionId: string, fingerprint: string, fetcher: typeof fetch = fetch) =>
  call<EdaImportRevision>(`/${encodeURIComponent(projectId)}/eda-imports/${encodeURIComponent(revisionId)}?fingerprint=${encodeURIComponent(fingerprint)}`, {}, fetcher);
export function deleteCatalogProject(projectId: string, projectFingerprint: string, fetcher: typeof fetch = fetch): Promise<ProjectDeletionResult> {
  return call(`/${encodeURIComponent(projectId)}`, { method: "DELETE", headers: { "X-Project-Fingerprint": projectFingerprint } }, fetcher);
}
export const fetchProjectContext = (projectId: string, fetcher: typeof fetch = fetch) => call<ProjectContext>(`/${encodeURIComponent(projectId)}/context`, {}, fetcher);
export const fetchConversations = (projectId: string, limit = 20, fetcher: typeof fetch = fetch) => call<{ items: ConversationSummary[]; next_cursor: string | null }>(`/${encodeURIComponent(projectId)}/conversations?limit=${limit}`, {}, fetcher);
export const fetchConversation = (projectId: string, conversationId: string, fetcher: typeof fetch = fetch) => call<ConversationDetail>(`/${encodeURIComponent(projectId)}/conversations/${encodeURIComponent(conversationId)}`, {}, fetcher);
export const decideReferenceProject = (projectId: string, conversationId: string, input: { project_identity: string; decision: ReferenceProjectDecisionAction; selected_features?: string[] }, fetcher: typeof fetch = fetch) => call<ConversationDetail>(`/${encodeURIComponent(projectId)}/conversations/${encodeURIComponent(conversationId)}/reference-projects/decision`, { method: "POST", body: JSON.stringify(input) }, fetcher);
export const decideFeature = (projectId: string, conversationId: string, input: { project_identity: string; feature: string; action: FeatureDecisionAction; value?: string | null }, fetcher: typeof fetch = fetch) => call<ConversationDetail>(`/${encodeURIComponent(projectId)}/conversations/${encodeURIComponent(conversationId)}/feature-decisions`, { method: "POST", body: JSON.stringify(input) }, fetcher);
export const proposeRequirementChanges = (projectId: string, conversationId: string, utterance: string, fetcher: typeof fetch = fetch) => call<ConversationDetail>(`/${encodeURIComponent(projectId)}/conversations/${encodeURIComponent(conversationId)}/requirement-change-proposals`, { method: "POST", body: JSON.stringify({ utterance }) }, fetcher);
export const confirmRequirementChanges = (projectId: string, conversationId: string, proposalId: string, proposalFingerprint: string, confirmed: boolean, fetcher: typeof fetch = fetch) => call<ConversationDetail>(`/${encodeURIComponent(projectId)}/conversations/${encodeURIComponent(conversationId)}/requirement-change-proposals/${encodeURIComponent(proposalId)}/confirmation`, { method: "POST", body: JSON.stringify({ proposal_fingerprint: proposalFingerprint, confirmed }) }, fetcher);
export const refineResearchIntent = (projectId: string, conversationId: string, input: { product_positioning: string; negative_constraints: string[] }, fetcher: typeof fetch = fetch) => call<ConversationDetail>(`/${encodeURIComponent(projectId)}/conversations/${encodeURIComponent(conversationId)}/research-intent-refinement`, { method: "POST", body: JSON.stringify(input) }, fetcher);
export const decideExecutionAuthorization = (projectId: string, conversationId: string, requestId: string, decision: "APPROVED" | "REJECTED", fetcher: typeof fetch = fetch) => call<ConversationDetail>(`/${encodeURIComponent(projectId)}/conversations/${encodeURIComponent(conversationId)}/execution-authorization/${encodeURIComponent(requestId)}/decision`, { method: "POST", body: JSON.stringify({ decision }) }, fetcher);
export const decideFirmwareSourceGeneration = (projectId: string, conversationId: string, decision: "APPROVED" | "REJECTED", fetcher: typeof fetch = fetch) => call<ConversationDetail>(`/${encodeURIComponent(projectId)}/conversations/${encodeURIComponent(conversationId)}/firmware-source-generation/decision`, { method: "POST", body: JSON.stringify({ decision }) }, fetcher);
export const decideSchematicProduction = (projectId: string, conversationId: string, decision: "APPROVED" | "REJECTED", fetcher: typeof fetch = fetch) => call<ConversationDetail>(`/${encodeURIComponent(projectId)}/conversations/${encodeURIComponent(conversationId)}/schematic-production/decision`, { method: "POST", body: JSON.stringify({ decision }) }, fetcher);
export const decideSchematicRepair = (projectId: string, conversationId: string, input: { repair_id: string; proposal_fingerprint: string; artifact_fingerprint: string; action: SchematicRepairDecisionAction; comment?: string; edited_proposal_fingerprint?: string | null }, fetcher: typeof fetch = fetch) => call<ConversationDetail>(`/${encodeURIComponent(projectId)}/conversations/${encodeURIComponent(conversationId)}/schematic-repair/decision`, { method: "POST", body: JSON.stringify(input) }, fetcher);
export type CircuitIntentSpecInput = {
  function: string;
  topology: string[];
  parameters: string[];
  calculation: string[];
  assumptions: string[];
  risks: string[];
  blocks?: Array<{ block_id: string; function: string; requirement_refs: string[]; architecture_refs: string[]; verification_refs: string[]; status?: "DRAFT" }>;
  decisions?: Array<{ decision_id: string; block_ref: string; decision_type: string; selected_option: string; rationale: string; alternatives?: string[]; tradeoffs?: string[]; calculation_refs?: string[]; evidence_refs?: string[]; risk_refs?: string[]; status?: "DRAFT" }>;
  calculations?: Array<{ calculation_id: string; owner_ref: string; equation: string; inputs?: Array<[string, string | number | boolean]>; outputs?: Array<[string, string | number | boolean]>; units?: string[]; corner?: string; margin?: string | number | boolean | null; acceptance_limit?: string | number | boolean | null; tool_reference?: string | null; model_reference?: string | null; evidence_refs?: string[]; status?: "PLANNED" | "UNKNOWN" }>;
  verification_requirements?: Array<{ verification_id: string; owner_ref: string; claim: string; verification_type: string; method: string; status?: "PLANNED"; currentness?: "CURRENT"; produced_evidence_refs?: [] }>;
};
export type CircuitIntentPreview = {
  project_id: string;
  conversation_id: string;
  turn_id: string;
  proposal_id: string;
  proposal_fingerprint: string;
  status: string;
  object_id: string;
  revision: string;
  base_revision: string | null;
  base_fingerprint: string | null;
  context_fingerprint: string;
  source_fingerprint: string;
  evidence_receipt_id: string;
  evidence_receipt_fingerprint: string;
  circuit_intent: CircuitIntentSpecInput;
  created_at: string;
};
export type CircuitIntentDecision = {
  project_id: string;
  proposal_id: string;
  proposal_fingerprint: string;
  status: "APPLIED" | "REJECTED";
  object_id: string | null;
  revision: string | null;
  object_fingerprint: string | null;
  approval_id: string;
  approval_fingerprint: string;
};
export const previewCircuitIntent = (
  projectId: string,
  input: { conversation_id: string; turn_id: string; circuit_intent: CircuitIntentSpecInput; erc_operation_id: string; object_id?: string | null; idempotency_key: string },
  fetcher: typeof fetch = fetch,
) => call<CircuitIntentPreview>(`/${encodeURIComponent(projectId)}/circuit-intents/preview`, { method: "POST", body: JSON.stringify(input) }, fetcher);
export const decideCircuitIntent = (
  projectId: string,
  input: { proposal_id: string; proposal_fingerprint: string; base_revision: string | null; base_fingerprint: string | null; context_fingerprint: string; evidence_receipt_fingerprint: string; decision: "APPROVE" | "REJECT" },
  fetcher: typeof fetch = fetch,
) => call<CircuitIntentDecision>(`/${encodeURIComponent(projectId)}/circuit-intents/decision`, { method: "POST", body: JSON.stringify(input) }, fetcher);

export type PredesignSourceExpectations = {
  requirement_context_fingerprint: string;
  architecture_id: string;
  architecture_fingerprint: string;
  hardware_selection_approval_id: string;
  hardware_truth_fingerprint: string;
};
export type PredesignCircuitPreview = Omit<CircuitIntentPreview, "context_fingerprint" | "evidence_receipt_id" | "evidence_receipt_fingerprint"> & {
  purpose: "PREDESIGN_APPROVAL";
  source_snapshot: Record<string, unknown>;
  completion: "DESIGN_ONLY";
};
export type PredesignCircuitDecision = CircuitIntentDecision & {
  purpose: "PREDESIGN_APPROVAL";
  completion: "DESIGN_ONLY";
};
export const previewPredesignCircuitIntent = (
  projectId: string,
  input: {
    purpose: "PREDESIGN_APPROVAL"; conversation_id: string; turn_id: string;
    circuit_intent: CircuitIntentSpecInput; idempotency_key: string;
    source_expectations: PredesignSourceExpectations;
    object_id?: string | null; base_revision?: string | null; base_fingerprint?: string | null;
  },
  fetcher: typeof fetch = fetch,
) => call<PredesignCircuitPreview>(`/${encodeURIComponent(projectId)}/circuit-intents/preview`, { method: "POST", body: JSON.stringify(input) }, fetcher);
export const decidePredesignCircuitIntent = (
  projectId: string,
  input: {
    purpose: "PREDESIGN_APPROVAL"; proposal_id: string; proposal_fingerprint: string;
    source_fingerprint: string; base_revision?: string | null; base_fingerprint?: string | null;
    decision: "APPROVE" | "REJECT";
  },
  fetcher: typeof fetch = fetch,
) => call<PredesignCircuitDecision>(`/${encodeURIComponent(projectId)}/circuit-intents/decision`, { method: "POST", body: JSON.stringify(input) }, fetcher);

export type SchematicVerificationInput = {
  intent_id: string; intent_revision: string; intent_fingerprint: string;
  plan_id: string; plan_fingerprint: string; artifact_fingerprint: string;
  generation_operation_id: string; erc_operation_id?: string | null;
  parse_operation_id?: string | null; readback_operation_id?: string | null; parameter_evidence_ids?: string[];
  library_map_evidence_ids?: string[]; library_map_evidence_fingerprints?: string[];
};
export type SchematicVerificationView = {
  record: {
    verification_id: string; project_id: string; conversation_id: string;
    profile_id?: "S02-I2C-SCHEMATIC" | null; profile_version?: number; required_claim_ids?: string[];
    library_map_evidence_ids?: string[]; library_map_evidence_fingerprints?: string[];
    parse_operation_id?: string | null; readback_operation_id?: string | null; parameter_evidence_ids?: string[];
    intent_id: string; intent_revision: string; intent_fingerprint: string;
    plan_id: string; plan_fingerprint: string; artifact_fingerprint: string;
    erc_status: "PASS" | "BLOCKED" | "FAILED";
    aggregate_status: "BLOCKED" | "FAILED" | "SCHEMATIC_VERIFIED";
    claims: Array<{
      claim_id: string;
      status: "PASS" | "BLOCKED";
      reason: "TRUSTED_CANONICAL_PROOF" | "NO_TRUSTED_RESOLVER" | "EVIDENCE_UNAVAILABLE" | "EVIDENCE_MISMATCH" | "SOURCE_STALE";
      evidence_ids: string[];
      proof?: {
        kind: "CATALOG_PARSE_RECEIPT" | "CANONICAL_ERC_RECEIPT" | "CANONICAL_XML_READBACK" | "REVIEWED_PARAMETER_CLAIM";
        scope: "SCHEMATIC_PARSE" | "SCHEMATIC_ERC" | "SCHEMATIC_READBACK" | "SCHEMATIC_PARAMETER";
        tenant_id: string; project_id: string; conversation_id: string;
        intent_id: string; intent_revision: string; intent_fingerprint: string;
        plan_id: string; plan_fingerprint: string; artifact_fingerprint: string;
        operation_id: string; source_receipt_id: string; source_receipt_fingerprint: string;
        verification_record_id: string; currentness_at_recording: "CURRENT";
        fingerprint: string;
      } | null;
    }>;
    fingerprint: string;
  };
  currentness: "CURRENT" | "STALE";
};
export const recordSchematicVerification = (
  projectId: string, conversationId: string, input: SchematicVerificationInput,
  fetcher: typeof fetch = fetch,
) => call<SchematicVerificationView>(`/${encodeURIComponent(projectId)}/conversations/${encodeURIComponent(conversationId)}/schematic-verifications`, { method: "POST", body: JSON.stringify(input) }, fetcher);
export const fetchSchematicVerification = (
  projectId: string, verificationId: string, fetcher: typeof fetch = fetch,
) => call<SchematicVerificationView>(`/${encodeURIComponent(projectId)}/schematic-verifications/${encodeURIComponent(verificationId)}`, {}, fetcher);
export const listSchematicVerifications = (
  projectId: string, conversationId: string, limit = 50, fetcher: typeof fetch = fetch,
) => call<SchematicVerificationView[]>(`/${encodeURIComponent(projectId)}/conversations/${encodeURIComponent(conversationId)}/schematic-verifications?limit=${limit}`, {}, fetcher);
export const fetchArtifactIntelligence = (projectId: string, conversationId: string, fetcher: typeof fetch = fetch) => call<ArtifactIntelligenceView>(`/${encodeURIComponent(projectId)}/conversations/${encodeURIComponent(conversationId)}/artifact-intelligence`, {}, fetcher);
export function submitArtifactIntelligence(projectId: string, conversationId: string, zipBase64: string | null = null, fetcher: typeof fetch = fetch): Promise<ArtifactIntelligenceView> {
  return call(`/${encodeURIComponent(projectId)}/conversations/${encodeURIComponent(conversationId)}/artifact-intelligence`, { method: "POST", body: JSON.stringify({ zip_base64: zipBase64 }) }, fetcher);
}
export const buildReverseDesignTruth = (projectId: string, conversationId: string, fetcher: typeof fetch = fetch) => call<{ design_revision: string; origin: "CREATE" | "REVERSE"; confirmed_mapping: string[]; unavailable: string[] }>(`/${encodeURIComponent(projectId)}/conversations/${encodeURIComponent(conversationId)}/artifact-intelligence/design-truth`, { method: "POST", body: "{}" }, fetcher);
export type ConversationDeletionResult = { conversation_id: string; deleted: boolean };
export function deleteConversation(projectId: string, conversationId: string, fetcher: typeof fetch = fetch): Promise<ConversationDeletionResult> {
  if (!projectId.trim() || !conversationId.trim()) throw new ProjectCatalogRequestError("CONVERSATION_ID_REQUIRED");
  return call(`/${encodeURIComponent(projectId)}/conversations/${encodeURIComponent(conversationId)}`, { method: "DELETE" }, fetcher);
}

export function previewProject(input: {
  name: string;
  parent_location?: Pick<ProjectLocation, "location_id" | "fingerprint">;
  // Keep the v5.2.7 request shape for older clients during the one-version migration.
  location?: Pick<ProjectLocation, "location_id" | "fingerprint">;
  project_type: Exclude<ProjectType, "EXISTING_FIRMWARE">;
  creation_mode: ProjectCreationMode;
  existing_directory: Pick<ProjectDirectory, "directory_id" | "fingerprint"> | null;
}, fetcher: typeof fetch = fetch): Promise<ProjectPreview> {
  return call("/previews", { method: "POST", body: JSON.stringify(input) }, fetcher);
}

export function confirmProject(preview: ProjectPreview, fetcher: typeof fetch = fetch): Promise<ProjectView> {
  return call("", { method: "POST", body: JSON.stringify({ preview_id: preview.preview_id, preview_fingerprint: preview.fingerprint, confirmed_at: new Date().toISOString() }) }, fetcher);
}

export function createConversation(projectId: string, fetcher: typeof fetch = fetch): Promise<ConversationSession> {
  return call(`/${encodeURIComponent(projectId)}/conversations`, { method: "POST", body: "{}" }, fetcher);
}

export function resumeConversation(projectId: string, conversationId: string, fetcher: typeof fetch = fetch): Promise<ConversationSession> {
  return call(`/${encodeURIComponent(projectId)}/conversations/${encodeURIComponent(conversationId)}/resume`, { method: "POST", body: "{}" }, fetcher);
}

export type SchematicPlanView = {
  plan_id: string; project_id: string; conversation_id: string; fingerprint: string;
  circuit_intent_id: string | null; circuit_intent_revision: string | null; circuit_intent_fingerprint: string | null;
  component_instances?: Array<{ instance_id: string; hardware_slot_id: string; design_truth_component_id: string; manufacturer: string; mpn: string; symbol: string; footprint: string }>;
  nets?: Array<{ net_id: string; name: string; endpoints: Array<{ component_id: string; pin_number: string }> }>;
};
export type SchematicPlanRevisionView = { schema_version: 2; revision_id: string; revision_number: number; predecessor_revision_id: string | null; plan_id: string; plan_fingerprint: string; is_current: boolean; currentness: "CURRENT" | "STALE" | "UNAVAILABLE"; planning: { status: "REVIEW" | "APPROVED" | "REJECTED"; proposal: SchematicPlanView; approved_plan?: SchematicPlanView | null; approval?: { decision: "APPROVED" | "REJECTED"; fingerprint: string } | null }; receipt_references: Array<{ kind: string; receipt_id: string; fingerprint: string }> };
export type SchematicPlanLegacyView = Omit<SchematicPlanRevisionView, "revision_id" | "revision_number" | "is_current" | "currentness"> & { revision_id: null; revision_number: 0; is_current: false; currentness: "STALE" };
export type SchematicPlanHistoryView = { schema_version: 2; head: SchematicPlanRevisionView | null; legacy: SchematicPlanLegacyView | null; history: SchematicPlanRevisionView[] };
export const fetchSchematicPlanHistory = (p: string,c: string,f: typeof fetch=fetch) => call<SchematicPlanHistoryView>(`/${encodeURIComponent(p)}/conversations/${encodeURIComponent(c)}/schematic-plan/currentness?schema_version=2`,{},f);
export const proposeSchematicPlanV2 = (p: string,c: string,input: { request_id: string; expected_head_revision_id: string | null; expected_head_plan_id: string | null; expected_head_plan_fingerprint: string | null },f: typeof fetch=fetch) => call<SchematicPlanRevisionView>(`/${encodeURIComponent(p)}/conversations/${encodeURIComponent(c)}/schematic-plan/proposal`,{method:"POST",body:JSON.stringify({schema_version:2,...input})},f);
export const decideSchematicPlanV2 = (p: string,c: string,input: { revision_id: string; plan_id: string; plan_fingerprint: string; decision: "APPROVED" | "REJECTED" },f: typeof fetch=fetch) => call<SchematicPlanRevisionView>(`/${encodeURIComponent(p)}/conversations/${encodeURIComponent(c)}/schematic-plan/decision`,{method:"POST",body:JSON.stringify({schema_version:2,...input})},f);
export type ProjectSupplyOption = { power_domain_id: string; domain_name: string; supply_net_ref: string; ground_net_ref: string };
export type ProjectSupplyRelation = { power_domain_id: string; source_component_refs: string[]; consumer_component_refs: string[]; nominal_voltage_v: string; supply_net_ref: string; ground_net_ref: string; evidence_refs: string[] };
export type ProjectSupplyInput = { design_truth_id: string; design_truth_fingerprint: string; selection_id: string; selection_fingerprint: string; circuit_id: string; circuit_fingerprint: string; plan_id: string; plan_fingerprint: string; relations: ProjectSupplyRelation[] };
export type EvidenceReview = { evidence_fingerprint?: string; candidate_fingerprint?: string; decision: "APPROVED" | "REJECTED"; fingerprint: string };
export type ProjectSupplyCandidate = ProjectSupplyInput & { schema_version?: 2 | 3; source_publications?: Array<{ source_id: string; generation: number; publication_fingerprint: string }>; candidate_id: string; tenant_id: string; project_id: string; conversation_id: string; fingerprint: string };
export type ProjectSupplyView = { status: string; current_options?: ProjectSupplyOption[]; candidate?: ProjectSupplyCandidate | null; review?: EvidenceReview | null };
export type DeviceConditions = SchematicParameterCandidate["conditions"];
export type StandardTarget = { component_instance_id: string; source_record_id: string; source_record_fingerprint: string; device_conditions: DeviceConditions };
export type StandardApplicabilityInput = { document_uri: string; document_revision: string; content_sha256: string; locator: SchematicParameterCandidate["locator"]; mode: "I2C_STANDARD"; frequency_max_hz?: string; rise_time_max_s?: string; cbus_max_f?: string; target_net_ids: string[]; target_components: StandardTarget[]; requirement_id: string; requirement_fingerprint: string; plan_id: string; plan_fingerprint: string };
export type LibraryTerminalBinding = { manufacturer_terminal: string; manufacturer_locator: SchematicParameterCandidate["locator"]; logical_symbol_pin: string; physical_pad_ids: string[]; net_id: string };
export type LibraryTerminalMapInput = { component_instance_id: string; source_record_id: string; source_record_fingerprint: string; plan_id: string; plan_fingerprint: string; symbol_library_id: string; symbol_uri: string; symbol_sha256: string; footprint_library_id: string; footprint_uri: string; footprint_sha256: string; terminal_bindings: LibraryTerminalBinding[] };
export type EvidenceView<T> = { evidence: T & { evidence_id: string; tenant_id: string; project_id: string; conversation_id: string; fingerprint: string }; review: EvidenceReview | null; currentness?: string };
export const fetchProjectSupply = (p: string,c: string,f: typeof fetch=fetch) => call<ProjectSupplyView>(`/${encodeURIComponent(p)}/conversations/${encodeURIComponent(c)}/project-supply`,{},f);
export const proposeProjectSupply = (p: string,c: string,input: ProjectSupplyInput,f: typeof fetch=fetch) => call<{ candidate: ProjectSupplyCandidate; review: EvidenceReview | null }>(`/${encodeURIComponent(p)}/conversations/${encodeURIComponent(c)}/project-supply`,{method:"POST",body:JSON.stringify(input)},f);
export const reviewProjectSupply = (p: string,c: string,id: string,fp: string,decision: "APPROVED"|"REJECTED",f: typeof fetch=fetch) => call<{ candidate: ProjectSupplyCandidate; review: EvidenceReview }>(`/${encodeURIComponent(p)}/conversations/${encodeURIComponent(c)}/project-supply/${encodeURIComponent(id)}/review`,{method:"POST",body:JSON.stringify({candidate_fingerprint:fp,decision})},f);
export const fetchStandardApplicability = (p: string,c: string,f: typeof fetch=fetch) => call<EvidenceView<StandardApplicabilityInput>[]>(`/${encodeURIComponent(p)}/conversations/${encodeURIComponent(c)}/standard-applicability`,{},f);
export const proposeStandardApplicability = (p: string,c: string,input: StandardApplicabilityInput,f: typeof fetch=fetch) => call<EvidenceView<StandardApplicabilityInput>>(`/${encodeURIComponent(p)}/conversations/${encodeURIComponent(c)}/standard-applicability`,{method:"POST",body:JSON.stringify(input)},f);
export const reviewStandardApplicability = (p: string,id: string,fp: string,decision: "APPROVED"|"REJECTED",f: typeof fetch=fetch) => call<EvidenceView<StandardApplicabilityInput>>(`/${encodeURIComponent(p)}/standard-applicability/${encodeURIComponent(id)}/review`,{method:"POST",body:JSON.stringify({evidence_fingerprint:fp,decision})},f);
export const fetchLibraryTerminalMaps = (p: string,c: string,f: typeof fetch=fetch) => call<EvidenceView<LibraryTerminalMapInput>[]>(`/${encodeURIComponent(p)}/conversations/${encodeURIComponent(c)}/library-terminal-maps`,{},f);
export const proposeLibraryTerminalMap = (p: string,c: string,input: LibraryTerminalMapInput,f: typeof fetch=fetch) => call<EvidenceView<LibraryTerminalMapInput>>(`/${encodeURIComponent(p)}/conversations/${encodeURIComponent(c)}/library-terminal-maps`,{method:"POST",body:JSON.stringify(input)},f);
export const reviewLibraryTerminalMap = (p: string,id: string,fp: string,decision: "APPROVED"|"REJECTED",f: typeof fetch=fetch) => call<EvidenceView<LibraryTerminalMapInput>>(`/${encodeURIComponent(p)}/library-terminal-maps/${encodeURIComponent(id)}/review`,{method:"POST",body:JSON.stringify({evidence_fingerprint:fp,decision})},f);
export const createLibraryReadbackOperation = (p: string,c: string,id: string,fp: string,f: typeof fetch=fetch) => call<SchematicEvidenceOperation>(`/${encodeURIComponent(p)}/conversations/${encodeURIComponent(c)}/schematic-evidence/operations`,{method:"POST",body:JSON.stringify({kind:"READBACK",library_map_evidence_id:id,library_map_evidence_fingerprint:fp})},f);

export type DrcBaselineInput = { import_revision_id: string; import_revision_fingerprint: string; pcb_relative_path: string };
export const createDrcBaselineOperation = (projectId: string, input: DrcBaselineInput, fetcher: typeof fetch = fetch) =>
  call<import("./executionClient").ExecutionOperationStatus>(`/${encodeURIComponent(projectId)}/eda-native/drc-baseline-operations`, { method: "POST", body: JSON.stringify(input) }, fetcher);


export type SelectionChoice = { slot_id: string; source_id: string; source_fingerprint: string };
export type SelectionRevisionHead = {
  revision_id: string; revision_number: number; selection_id: string; selection_fingerprint: string;
  fingerprint: string; status: "REVIEW" | "APPROVED" | "REJECTED" | "REVOKED";
  decision_fingerprint: string | null; currentness: "CURRENT" | "STALE"; reason: string | null;
};
export type SelectionPreview = {
  kind: "PREVIEW"; schema_version: 2; project_id: string; conversation_id: string;
  expected_head: [string | null, string | null, string | null]; preview_fingerprint: string;
  required_slots: string[]; choices: SelectionChoice[];
};
export type SelectionPreviewV3 = Omit<SelectionPreview,"schema_version"> & {
  schema_version: 3; root_slots: string[]; selected_parents: SelectionChoice[];
};
export type SelectionTreePreview = SelectionPreview | SelectionPreviewV3;
export type SelectionRevisionView = {
  kind: "REVISION"; schema_version: 2; head: SelectionRevisionHead;
  selection: ConversationDetail["hardware_selection"];
};
export type SelectionCurrent = SelectionRevisionView | {
  kind: "LEGACY"; schema_version: 2; expected_head: SelectionPreview["expected_head"];
  selection: ConversationDetail["hardware_selection"]; currentness: "UNVERIFIED"; reason: string;
};
const selectionPath = (projectId: string, conversationId: string) =>
  `/${encodeURIComponent(projectId)}/conversations/${encodeURIComponent(conversationId)}/hardware-selection`;
export async function fetchSelectionPreview(projectId: string, conversationId: string, fetcher: typeof fetch = fetch): Promise<SelectionPreview> {
  const result = await call<SelectionPreview>(selectionPath(projectId, conversationId) + "/currentness?view=PREVIEW", {}, fetcher);
  if (result.kind !== "PREVIEW" || result.schema_version !== 2 || result.project_id !== projectId || result.conversation_id !== conversationId
      || !Array.isArray(result.expected_head) || result.expected_head.length !== 3 || !Array.isArray(result.required_slots)
      || !Array.isArray(result.choices) || !/^sha256:[a-f0-9]{64}$/.test(result.preview_fingerprint)) throw new Error("SELECTION_PREVIEW_INVALID");
  return result;
}
export async function fetchSelectionParentPreview(projectId:string,conversationId:string,parents?:SelectionChoice[],fetcher:typeof fetch=fetch):Promise<SelectionPreviewV3> {
  const params=new URLSearchParams({view:"PREVIEW",schema_version:"3"});
  if(parents)params.set("parent_choices",JSON.stringify([...parents].sort((a,b)=>a.slot_id.localeCompare(b.slot_id))));
  const result=await call<SelectionPreviewV3>(selectionPath(projectId,conversationId)+"/currentness?"+params.toString(),{},fetcher);
  if(result.kind!=="PREVIEW"||result.schema_version!==3||result.project_id!==projectId||result.conversation_id!==conversationId
    ||!Array.isArray(result.expected_head)||result.expected_head.length!==3||!Array.isArray(result.required_slots)||!Array.isArray(result.choices)
    ||!Array.isArray(result.root_slots)||!Array.isArray(result.selected_parents)||!/^sha256:[a-f0-9]{64}$/.test(result.preview_fingerprint))throw new Error("SELECTION_PREVIEW_INVALID");
  return result;
}
export const fetchSelectionCurrent = (projectId: string, conversationId: string, fetcher: typeof fetch = fetch) =>
  call<SelectionCurrent>(selectionPath(projectId, conversationId) + "/currentness", {}, fetcher);
export const fetchSelectionHistory = (projectId: string, conversationId: string, fetcher: typeof fetch = fetch) =>
  call<{kind: "HISTORY"; schema_version: 2; revisions: SelectionRevisionView[]}>(selectionPath(projectId, conversationId) + "/currentness?view=HISTORY", {}, fetcher);
export const proposeSelectionRevision = (projectId: string, conversationId: string, preview: SelectionTreePreview, choices: SelectionChoice[], requestId: string, fetcher: typeof fetch = fetch) =>
  call<SelectionRevisionView>(selectionPath(projectId, conversationId) + "/proposal", {method: "POST", body: JSON.stringify({schema_version: preview.schema_version, ...(preview.schema_version===3?{parent_choices:preview.selected_parents}:{}), request_id: requestId,
    expected_head_revision_id: preview.expected_head[0], expected_head_selection_id: preview.expected_head[1],
    expected_head_selection_fingerprint: preview.expected_head[2], preview_fingerprint: preview.preview_fingerprint, selections: choices})}, fetcher);
export const decideSelectionRevision = (projectId: string, conversationId: string, head: SelectionRevisionHead, decision: "APPROVED" | "REJECTED" | "REVOKED", fetcher: typeof fetch = fetch) =>
  call<SelectionRevisionView>(selectionPath(projectId, conversationId) + "/decision", {method: "POST", body: JSON.stringify({schema_version: 2,
    revision_id: head.revision_id, selection_id: head.selection_id, selection_fingerprint: head.selection_fingerprint,
    expected_decision_fingerprint: head.decision_fingerprint, decision})}, fetcher);
export const requestHardwareModelCandidateV2 = (projectId: string, conversationId: string, preview: SelectionTreePreview,
  input: {request_id: string; plan_id: string; phase_id: string; plan_task_id: string}, fetcher: typeof fetch = fetch) =>
  call<{status: string; operation_id: string}>(selectionPath(projectId, conversationId) + "/model-candidate-proposal", {method: "POST", body: JSON.stringify({
    ...input, schema_version: preview.schema_version, ...(preview.schema_version===3?{parent_choices:preview.selected_parents}:{}), expected_head_revision_id: preview.expected_head[0], expected_head_selection_id: preview.expected_head[1],
    expected_head_selection_fingerprint: preview.expected_head[2], preview_fingerprint: preview.preview_fingerprint})}, fetcher);
