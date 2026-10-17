import { Panel, ContextStatus } from "../../design-system/components";
import type { ReactNode } from "react";
import type { ArchitectureDesign, ArtifactIntelligenceView, CapabilityEvaluation, DesignTruth, EngineeringKnowledgeModel, EngineeringPlan, EngineeringWorkflow, ExecutionAuthorization, ExecutionRequest, FirmwareSourceGenerationState, FirmwareSpecification, HardwareDesignSpecification, ImplementationSpecification, ProjectContext, ProjectView, RequirementContext, SchematicProductionState, SimulationSpecification, TeachingContext, VerificationSpecification } from "../api/projectClient";
import type { EngineeringMemoryProjection } from "../memory/memoryViewModel";
import { formatMemoryDate, memorySourceLabel, normalizeEngineeringMemoryProjection } from "../memory/memoryViewModel";
import { identityPlatform, type EngineeringContextProjection, type ProjectWorkspaceIdentity } from "../projectWorkspaceIdentity";
import type { WorkbenchResourceNode } from "../api/workbenchClient";
import type { WorkflowView } from "../workflow/engineeringWorkflowModel";
import { useExecutionEvents } from "../execution/ExecutionEventProvider";

const workflowLabels = {
  requirement: "Requirement Analysis",
  architecture: "Architecture Design",
  hardware: "Hardware Design",
  firmware: "Firmware Design",
  verification: "Verification",
} as const;

const activityLabels: Record<string, string> = {
  OPERATION_CREATED: "任务已创建",
  APPROVAL_ISSUED: "已获得确认",
  OPERATION_QUEUED: "等待执行",
  OPERATION_CLAIMED: "任务已接收",
  OPERATION_STARTED: "开始执行",
  CANCELLATION_REQUESTED: "收到取消请求",
  CANCELLATION_PENDING: "正在取消",
  OPERATION_SUCCEEDED: "执行完成",
  OPERATION_FAILED: "执行未完成",
  OPERATION_CANCELLED: "执行已取消",
  OPERATION_TIMED_OUT: "执行超时",
  RECOVERY_REQUIRED: "需要恢复",
  RECOVERY_RESOLVED: "恢复完成",
  RESYNC_REQUIRED: "需要重新同步",
};

function categoryMatches(category: string, target: string): boolean {
  return category.toLocaleUpperCase().includes(target);
}

function referenceLabels(context: ProjectContext | null, target: string): string[] {
  return (context?.groups ?? [])
    .filter((group) => categoryMatches(group.category, target))
    .flatMap((group) => group.references.map((reference) => reference.display_label))
    .filter(Boolean)
    .slice(0, 8);
}

function resourceLabels(resources: WorkbenchResourceNode[], target: string): string[] {
  return resources.filter((resource) => categoryMatches(resource.category, target)).map((resource) => resource.name).slice(0, 8);
}

function ContextItems({ items, empty }: { items: string[]; empty: string }) {
  return items.length ? <ul className="embrix-context-items">{items.map((item) => <li key={item}>{item}</li>)}</ul> : <p className="ide-list-empty">{empty}</p>;
}

function hasText(value: string | null | undefined): boolean {
  return Boolean(value?.trim());
}

const requirementStatusLabels: Record<string, string> = {
  DRAFT: "草稿",
  ANALYZING: "分析中",
  CLARIFICATION_REQUIRED: "待澄清",
  CONFIRMED: "已确认",
  ARCHIVED: "已归档",
};

const lifecycleStatusLabels: Record<string, string> = {
  DISCOVERY_STARTED: "草稿",
  REQUIREMENTS_PARTIAL: "待澄清",
  REQUIREMENTS_REFINING: "分析中",
  REQUIREMENTS_READY: "已确认",
  DESIGN_READY: "已确认",
};

const requirementValueLabels: Record<string, string> = {
  LINE_FOLLOWING: "巡线",
  OBSTACLE_AVOIDANCE: "避障",
  REMOTE_CONTROL: "遥控",
  AUTONOMOUS_NAVIGATION: "自动导航",
  TEMPERATURE_SENSING: "测温",
  TEMPERATURE_CONTROL: "温度控制",
  STATUS_DISPLAY: "状态显示",
  LOW_POWER: "低功耗",
  BATTERY: "电池供电",
  MOBILE_ROBOT: "移动机器人",
};

const requirementCategoryLabels: Record<string, string> = {
  TARGET_PLATFORM: "平台",
  HARDWARE_SCOPE: "硬件范围",
  RTOS: "系统",
  POWER_SOURCE: "供电",
  ACCURACY: "精度",
  BATTERY_LIFE: "续航",
  BUDGET: "预算",
  FORM_FACTOR: "尺寸",
  POWER_CONSUMPTION: "功耗",
  RESPONSE_TIME: "响应时间",
  WEIGHT: "重量",
  COMMUNICATION: "通信",
  REMOTE_MONITORING: "远程监控",
  CONTROL_MODE: "控制方式",
};

function requirementValue(value: string): string {
  return requirementValueLabels[value] ?? value;
}

function CurrentRequirement({ requirement }: { requirement: RequirementContext | null | undefined }) {
  if (!requirement) return null;
  const activeItems = [...requirement.requirements, ...requirement.constraints, ...requirement.proposed_items]
    .filter((item) => item.status !== "SUPERSEDED");
  const criteria = requirement.acceptance_criteria ?? [];
  const conflicts = requirement.conflicts ?? [];
  if (!activeItems.length && !requirement.open_questions.length && !criteria.length && !conflicts.length) return null;
  const confirmedItems = activeItems.filter((item) => item.status === "CONFIRMED");
  const goal = confirmedItems.find((item) => item.category === "PRODUCT_GOAL")?.normalized_value ?? null;
  const functions = confirmedItems.filter((item) => item.category === "FUNCTIONAL").map((item) => requirementValue(item.normalized_value));
  const constraints = confirmedItems.filter((item) => item.category !== "PRODUCT_GOAL" && item.category !== "FUNCTIONAL").map((item) => `${requirementCategoryLabels[item.category] ?? "工程约束"}: ${requirementValue(item.normalized_value)}`);
  const lifecycleStatus = lifecycleStatusLabels[requirement.lifecycle] ?? "草稿";
  const status = requirement.status === "DRAFT" && lifecycleStatus !== "草稿"
    ? lifecycleStatus
    : requirementStatusLabels[requirement.status ?? ""] ?? lifecycleStatus;
  return <section className="embrix-current-requirement" aria-label="CURRENT REQUIREMENT">
    <h3>CURRENT REQUIREMENT</h3>
    <dl className="ide-project-facts">
      <div><dt>目标</dt><dd>{goal ?? "尚未形成项目目标"}</dd></div>
      <div><dt>状态</dt><dd>{status}</dd></div>
      <div><dt>完成度</dt><dd>{confirmedItems.length}/{activeItems.length} 项已确认</dd></div>
    </dl>
    {functions.length ? <div className="embrix-requirement-subsection"><strong>核心功能</strong><ContextItems items={functions} empty="" /></div> : null}
    {constraints.length ? <div className="embrix-requirement-subsection"><strong>约束与范围</strong><ContextItems items={constraints} empty="" /></div> : null}
    {requirement.open_questions.length ? <div className="embrix-requirement-subsection"><strong>待澄清（{requirement.open_questions.length}）</strong><ContextItems items={requirement.open_questions.slice(0, 5).map((item) => item.question)} empty="" /></div> : null}
    {conflicts.length ? <div className="embrix-requirement-subsection"><strong>冲突（{conflicts.length}）</strong><ContextItems items={conflicts.map((item) => item.description)} empty="" /></div> : null}
    {criteria.length ? <div className="embrix-requirement-subsection"><strong>验收标准（{criteria.length}）</strong><ul className="embrix-context-items">{criteria.slice(0, 4).map((item, index) => <li key={`${item.then}-${index}`}><span>Given</span> {item.given}；<span>When</span> {item.when}；<span>Then</span> {item.then}<small>验证：{item.verification}</small></li>)}</ul></div> : null}
  </section>;
}

const planStatusLabels: Record<string, string> = {
  DRAFT: "草稿",
  GENERATING: "生成中",
  PROPOSED: "待用户审批",
  REVIEWING: "审核中",
  APPROVED: "已批准",
  EXECUTING: "执行状态不可用",
  COMPLETED: "已完成",
  REJECTED: "已拒绝",
  CANCELLED: "已取消",
  SUPERSEDED: "已替代",
};

function CurrentPlan({ plan }: { plan: EngineeringPlan | null | undefined }) {
  if (!plan || (!hasText(plan.title) && !plan.phases.length && !plan.verification_strategy.length)) return null;
  const phases = plan.phases ?? [];
  const completed = phases.filter((phase) => phase.status === "COMPLETED").length;
  const nextIndex = phases.findIndex((phase) => phase.status !== "COMPLETED" && phase.status !== "CANCELLED");
  const next = nextIndex >= 0 ? phases[nextIndex] : null;
  const upcoming = nextIndex >= 0 ? phases.slice(nextIndex + 1, nextIndex + 4) : [];
  const approval = plan.status === "APPROVED" ? "用户已批准" : plan.status === "PROPOSED" || plan.status === "REVIEWING" ? "等待用户审批" : null;
  return <section className="embrix-current-plan" aria-label="CURRENT PLAN">
    <h3>CURRENT PLAN</h3>
    <dl className="ide-project-facts">
      <div><dt>计划</dt><dd>{plan.title}</dd></div>
      <div><dt>状态</dt><dd>{planStatusLabels[plan.status] ?? "计划状态"}</dd></div>
      <div><dt>进度</dt><dd>{completed}/{phases.length} 个阶段已完成</dd></div>
      {approval && <div><dt>审批</dt><dd>{approval}</dd></div>}
    </dl>
    {next ? <div className="embrix-plan-subsection"><strong>Next</strong><p>{next.name}</p></div> : null}
    {upcoming.length ? <div className="embrix-plan-subsection"><strong>Upcoming</strong><ContextItems items={upcoming.map((phase) => phase.name)} empty="" /></div> : null}
  </section>;
}

const architectureStatusLabels: Record<string, string> = {
  DRAFT: "草稿",
  GENERATING: "生成中",
  PROPOSED: "待用户审批",
  REVIEWING: "审核中",
  APPROVED: "已批准",
  IMPLEMENTING: "实施状态不可用",
  COMPLETED: "已完成",
  REJECTED: "已拒绝",
  CANCELLED: "已取消",
  SUPERSEDED: "已替代",
  INVALIDATED: "已失效",
};

function CurrentArchitecture({ architecture }: { architecture: ArchitectureDesign | null | undefined }) {
  if (!architecture || (!hasText(architecture.title) && !hasText(architecture.objective) && !architecture.system_components.length && !architecture.interfaces.length && !architecture.decisions.length && !architecture.constraints.length)) return null;
  const components = architecture.system_components ?? [];
  const componentNames = new Map(components.map((item) => [item.component_id, item.name]));
  const interfaces = (architecture.interfaces ?? []).map((item) => `${componentNames.get(item.source_component_id) ?? "组件"} → ${componentNames.get(item.target_component_id) ?? "组件"}（${item.protocol === "UNKNOWN" ? "待明确" : item.protocol}）`);
  const reviewLabel = architecture.status === "APPROVED" ? "用户已批准" : architecture.status === "PROPOSED" || architecture.status === "REVIEWING" ? "等待用户审批" : null;
  return <section className="embrix-current-architecture" aria-label="CURRENT ARCHITECTURE">
    <h3>CURRENT ARCHITECTURE</h3>
    <dl className="ide-project-facts">
      <div><dt>架构</dt><dd>{architecture.title}</dd></div>
      <div><dt>状态</dt><dd>{architectureStatusLabels[architecture.status] ?? "架构状态"}</dd></div>
      {reviewLabel && <div><dt>评审</dt><dd>{reviewLabel}</dd></div>}
    </dl>
    {hasText(architecture.objective) ? <div className="embrix-architecture-subsection"><strong>Architecture Summary</strong><p>{architecture.objective}</p></div> : null}
    {components.length ? <div className="embrix-architecture-subsection"><strong>Components</strong><ContextItems items={components.slice(0, 8).map((item) => item.name)} empty="" /></div> : null}
    {interfaces.length ? <div className="embrix-architecture-subsection"><strong>Interfaces</strong><ContextItems items={interfaces.slice(0, 8)} empty="" /></div> : null}
  </section>;
}

function CurrentDesignTruth({ designTruth }: { designTruth: DesignTruth | null | undefined }) {
  const section = (title: string, items: string[], empty: string) => <div className="embrix-architecture-subsection"><strong>{title}</strong><ContextItems items={items} empty={empty} /></div>;
  if (!designTruth) return null;
  const componentNames = new Map(designTruth.components.map((item) => [item.component_id, item.name]));
  const labelComponent = (value: string) => componentNames.get(value) ?? "组件";
  const mapItems = (level: "EXPLICIT" | "INFERRED") => {
    const items: string[] = [];
    for (const item of designTruth.components.filter((candidate) => candidate.truth_level === level)) items.push(`组件：${item.name}`);
    for (const item of designTruth.interfaces.filter((candidate) => candidate.truth_level === level)) items.push(`接口：${labelComponent(item.source)} → ${labelComponent(item.target)}（${item.protocol === "UNKNOWN" ? "协议待确认" : item.protocol}）`);
    for (const item of designTruth.signals.filter((candidate) => candidate.truth_level === level)) items.push(`信号：${item.name}（${labelComponent(item.source)} → ${item.targets.map(labelComponent).join("、") || "目标待确认"}）`);
    for (const item of designTruth.pins.filter((candidate) => candidate.truth_level === level)) items.push(`引脚：${labelComponent(item.component_id)} · ${item.pin_name}（${item.function}）`);
    for (const item of designTruth.peripherals.filter((candidate) => candidate.truth_level === level)) items.push(`外设：${item.peripheral}（${item.function}）`);
    for (const item of designTruth.nets.filter((candidate) => candidate.truth_level === level)) items.push(`网络：${item.net_name}（${item.purpose}）`);
    for (const item of designTruth.power_domains.filter((candidate) => candidate.truth_level === level)) items.push(`供电：${item.name}（${item.purpose}）`);
    for (const item of designTruth.firmware_bindings.filter((candidate) => candidate.truth_level === level)) items.push(`固件：${item.firmware_symbol} → ${item.function}`);
    return items.slice(0, 12);
  };
  const confirmed = mapItems("EXPLICIT");
  const suggested = mapItems("INFERRED");
  const unavailable: string[] = [];
  if (!designTruth.pins.length && !designTruth.peripherals.length && !designTruth.nets.length) unavailable.push("Hardware Mapping：暂无已确认硬件映射，需要原理图或 PCB 证据");
  if (!designTruth.firmware_bindings.length) unavailable.push("Firmware Mapping：没有 Firmware 证据");
  if (designTruth.interfaces.some((item) => item.truth_level === "UNKNOWN")) unavailable.push("部分接口协议待确认，需要结构化硬件或固件证据");
  const truthEntities = [...designTruth.components, ...designTruth.interfaces, ...designTruth.signals, ...designTruth.pins, ...designTruth.peripherals, ...designTruth.nets, ...designTruth.power_domains, ...designTruth.firmware_bindings];
  if (!truthEntities.length) return null;
  if (truthEntities.some((item) => item.truth_level === "UNKNOWN")) unavailable.push("部分工程实体仍未知，需要相应 artifact 证据");
  if (truthEntities.some((item) => item.truth_level === "INVALIDATED")) unavailable.push("存在已失效映射，需要新的设计版本重新确认");
  return <section className="embrix-current-design-truth" aria-label="CURRENT DESIGN TRUTH">
    <h3>CURRENT DESIGN TRUTH</h3>
    <dl className="ide-project-facts"><div><dt>设计版本</dt><dd>{designTruth.design_revision}</dd></div><div><dt>事实范围</dt><dd>{designTruth.origin === "REVERSE" ? "基于已批准 Artifact Evidence" : "基于已批准架构的确定性投影"}</dd></div></dl>
    {confirmed.length ? section("Confirmed Mapping", confirmed, "") : null}
    {suggested.length ? section("Suggested Mapping", suggested, "") : null}
    {unavailable.length ? section("Unavailable", unavailable, "") : null}
  </section>;
}

const hardwareDomainTypeLabels: Record<string, string> = {
  SYSTEM: "System",
  CONTROLLER: "Controller",
  POWER: "Power",
  SENSOR: "Sensor",
  ACTUATOR: "Actuator",
  COMMUNICATION: "Communication",
  MECHANICAL: "Mechanical",
  OTHER: "Other",
};

const hardwareInterfaceTypeLabels: Record<string, string> = {
  POWER: "Power",
  GPIO: "GPIO",
  BUS: "Bus",
  CONTROL: "Control",
  CLOCK: "Clock",
  DEBUG: "Debug",
  COMMUNICATION: "Communication",
  ANALOG: "Analog",
  DIGITAL: "Digital",
  UNKNOWN: "Unknown",
};

const hardwareDecisionStatusLabels: Record<string, string> = {
  OPEN: "Pending",
  CONFIRMED: "Confirmed",
  REJECTED: "Rejected",
};

export function HardwareDesignSpecificationPanel({ spec }: { spec: HardwareDesignSpecification | null | undefined }) {
  const domains = spec?.hardware_domains ?? [];
  const domainNames = new Map(domains.map((domain) => [domain.domain_id, domain.name]));
  const interfaces = spec?.interfaces ?? [];
  const decisions = spec?.decisions ?? [];
  if (!spec || (!domains.length && !interfaces.length && !decisions.length)) return null;
  return <section className="embrix-hardware-design-specification" aria-label="HARDWARE DESIGN">
    <h3>HARDWARE DESIGN</h3>
    <>
      {domains.length ? <div className="embrix-architecture-subsection">
        <strong>Hardware Domains</strong>
        <ul className="embrix-context-items">{domains.map((domain) => <li key={domain.domain_id}><strong>{domain.name}</strong><small>{hardwareDomainTypeLabels[domain.type] ?? "Other"}</small>{domain.requirements.map((requirement) => <p key={requirement}>{requirement}</p>)}</li>)}</ul>
      </div> : null}
      {interfaces.length ? <div className="embrix-architecture-subsection">
        <strong>Interfaces</strong>
        <ul className="embrix-context-items">{interfaces.map((item) => <li key={item.interface_id}><strong>{hardwareInterfaceTypeLabels[item.interface_type] ?? "Unknown"}</strong><span>{domainNames.get(item.source) ?? "Unknown domain"} → {domainNames.get(item.target) ?? "Unknown domain"}</span></li>)}</ul>
      </div> : null}
      {decisions.length ? <div className="embrix-architecture-subsection">
        <strong>Design Decisions</strong>
        <ul className="embrix-context-items">{decisions.map((decision) => <li key={decision.decision_id}><strong>{decision.subject}</strong><small>{hardwareDecisionStatusLabels[decision.status] ?? "Pending"}</small></li>)}</ul>
      </div> : null}
    </>
  </section>;
}

const firmwareModuleTypeLabels: Record<string, string> = {
  APPLICATION: "Application",
  CONTROL: "Control",
  DRIVER: "Driver",
  HAL: "HAL",
  COMMUNICATION: "Communication",
  MIDDLEWARE: "Middleware",
  BOOT: "Boot",
  UNKNOWN: "Unknown",
};

export function FirmwareDesignSpecificationPanel({ spec, hardwareSpec }: { spec: FirmwareSpecification | null | undefined; hardwareSpec?: HardwareDesignSpecification | null }) {
  const modules = spec?.modules ?? [];
  const moduleNames = new Map(modules.map((module) => [module.module_id, module.name]));
  const hardwareInterfaces = new Map((hardwareSpec?.interfaces ?? []).map((item) => [item.interface_id, item]));
  const domainNames = new Map((hardwareSpec?.hardware_domains ?? []).map((domain) => [domain.domain_id, domain.name]));
  if (!spec || (!modules.length && !spec.bindings.length && !spec.verification_requirements.length)) return null;
  return <section className="embrix-firmware-design-specification" aria-label="FIRMWARE DESIGN">
    <h3>FIRMWARE DESIGN</h3>
    <>
      {modules.length ? <div className="embrix-architecture-subsection">
        <strong>Firmware Architecture</strong>
        <ul className="embrix-context-items">{modules.map((module) => <li key={module.module_id}><strong>{module.name}</strong><small>{firmwareModuleTypeLabels[module.module_type] ?? "Unknown"}</small><p>{module.responsibility}</p></li>)}</ul>
      </div> : null}
      {spec.bindings.length ? <div className="embrix-architecture-subsection">
        <strong>Hardware Binding</strong>
        <ul className="embrix-context-items">{spec.bindings.map((binding) => {
          const hardwareInterface = hardwareInterfaces.get(binding.hardware_interface_id);
          const source = hardwareInterface ? (domainNames.get(hardwareInterface.source) ?? "Unknown domain") : "Unknown interface";
          const target = hardwareInterface ? (domainNames.get(hardwareInterface.target) ?? "Unknown domain") : "Unknown domain";
          return <li key={binding.binding_id}><strong>{moduleNames.get(binding.firmware_module_id) ?? "Firmware module"}</strong><span>{source} → {target}</span></li>;
        })}</ul>
      </div> : null}
      {spec.verification_requirements.length ? <div className="embrix-architecture-subsection">
        <strong>Verification</strong>
        <ul className="embrix-context-items">{spec.verification_requirements.map((item) => <li key={item.verification_id}><strong>{item.method}</strong><small>Pending</small></li>)}</ul>
      </div> : null}
    </>
  </section>;
}

const simulationTargetTypeLabels: Record<string, string> = {
  CONTROLLER: "Controller",
  SENSOR: "Sensor",
  ACTUATOR: "Actuator",
  COMMUNICATION: "Communication",
  POWER: "Power",
  FIRMWARE_MODULE: "Firmware Module",
  SYSTEM: "System",
  UNKNOWN: "Unknown",
};

const simulationScenarioTypeLabels: Record<string, string> = {
  FUNCTIONAL_TEST: "Functional Test",
  INTERFACE_TEST: "Interface Test",
  CONTROL_TEST: "Control Test",
  COMMUNICATION_TEST: "Communication Test",
  PERFORMANCE_TEST: "Performance Test",
  UNKNOWN: "Unknown",
};

const verificationCaseTypeLabels: Record<string, string> = {
  FUNCTIONAL: "Functional",
  INTERFACE: "Interface",
  CONTROL: "Control",
  COMMUNICATION: "Communication",
  PERFORMANCE: "Performance",
  DESIGN_REVIEW: "Design Review",
};

const verificationEvidenceTypeLabels: Record<string, string> = {
  LOG: "Log",
  TRACE: "Trace",
  WAVEFORM: "Waveform",
  MEASUREMENT: "Measurement",
  SIMULATION_REPORT: "Simulation Report",
  TEST_REPORT: "Test Report",
  DOCUMENT: "Document",
  UNKNOWN: "Evidence",
};

export function SimulationDesignSpecificationPanel({ spec }: { spec: SimulationSpecification | null | undefined }) {
  const models = new Map((spec?.models ?? []).map((model) => [model.model_id, model]));
  const targets = (spec?.targets ?? []).map((modelId) => models.get(modelId)).filter((model): model is NonNullable<typeof model> => Boolean(model));
  if (!spec || (!targets.length && !spec.scenarios.length && !spec.evidence_requirements.length)) return null;
  return <section className="embrix-simulation-design-specification" aria-label="SIMULATION DESIGN">
    <h3>SIMULATION DESIGN</h3>
    <>
      {targets.length ? <div className="embrix-architecture-subsection">
        <strong>Simulation Targets</strong>
        <ul className="embrix-context-items">{targets.map((model) => <li key={model.model_id}><strong>{model.name}</strong><small>{simulationTargetTypeLabels[model.model_type] ?? "Unknown"}</small></li>)}</ul>
      </div> : null}
      {spec.scenarios.length ? <div className="embrix-architecture-subsection">
        <strong>Scenarios</strong>
        <ul className="embrix-context-items">{spec.scenarios.map((scenario) => <li key={scenario.scenario_id}><strong>{scenario.title}</strong><small>{simulationScenarioTypeLabels[scenario.scenario_type] ?? "Unknown"}</small></li>)}</ul>
      </div> : null}
      {spec.evidence_requirements.length ? <div className="embrix-architecture-subsection">
        <strong>Evidence</strong>
        <ul className="embrix-context-items">{spec.evidence_requirements.map((item) => <li key={item.evidence_id}><strong>{item.description}</strong><small>Pending</small></li>)}</ul>
      </div> : null}
    </>
  </section>;
}

export function VerificationSpecificationPanel({ spec }: { spec: VerificationSpecification | null | undefined }) {
  if (!spec || (!spec.cases.length && !spec.evidence_requirements.length)) return null;
  return <section className="embrix-verification-specification" aria-label="VERIFICATION PLAN">
    <h3>VERIFICATION PLAN</h3>
    <>
      {spec.cases.length ? <div className="embrix-architecture-subsection">
        <strong>Verification Cases</strong>
        <ul className="embrix-context-items">{spec.cases.map((item) => <li key={item.case_id}><strong>{item.title}</strong><small>{verificationCaseTypeLabels[item.case_type] ?? "Verification"}</small></li>)}</ul>
      </div> : null}
      {spec.evidence_requirements.length ? <div className="embrix-architecture-subsection">
        <strong>Evidence Required</strong>
        <ul className="embrix-context-items">{spec.evidence_requirements.map((item) => <li key={item.evidence_id}><strong>{verificationEvidenceTypeLabels[item.evidence_type] ?? "Evidence"}</strong><small>Pending</small></li>)}</ul>
      </div> : null}
      <div className="embrix-architecture-subsection"><strong>Status</strong><p>Pending</p></div>
    </>
  </section>;
}

const implementationTaskTypeLabels: Record<string, string> = {
  HARDWARE_CONFIGURATION: "Hardware Configuration",
  FIRMWARE_MODULE: "Firmware Module",
  DRIVER_IMPLEMENTATION: "Driver Implementation",
  INTERFACE_IMPLEMENTATION: "Interface Implementation",
  CONTROL_ALGORITHM: "Control Algorithm",
  COMMUNICATION: "Communication",
  CONFIGURATION: "Configuration",
  TEST_PREPARATION: "Test Preparation",
  DOCUMENTATION: "Documentation",
};

const implementationIntentLabels: Record<string, string> = {
  MODULE_IMPLEMENTATION: "Module Implementation",
  INTERFACE_ADAPTATION: "Interface Adaptation",
  DRIVER_DEVELOPMENT: "Driver Development",
  CONTROL_IMPLEMENTATION: "Control Implementation",
  CONFIGURATION_SETUP: "Configuration Setup",
  TEST_PREPARATION: "Test Preparation",
  DOCUMENTATION: "Documentation",
};

const implementationArtifactTypeLabels: Record<string, string> = {
  SOURCE_CODE: "Source Code",
  CONFIGURATION: "Configuration",
  SCHEMATIC: "Schematic",
  PCB: "PCB",
  DOCUMENT: "Document",
  TEST_ASSET: "Test Asset",
  SIMULATION_ASSET: "Simulation Asset",
  UNKNOWN: "Unknown",
};

export function ImplementationSpecificationPanel({ spec, verificationSpec }: { spec: ImplementationSpecification | null | undefined; verificationSpec?: VerificationSpecification | null }) {
  const cases = new Map((verificationSpec?.cases ?? []).map((item) => [item.case_id, item.title]));
  if (!spec || (!spec.tasks.length && !spec.expected_artifacts.length && !spec.verification_refs.length)) return null;
  return <section className="embrix-implementation-specification" aria-label="IMPLEMENTATION PLAN">
    <h3>IMPLEMENTATION PLAN</h3>
    <>
      {spec.tasks.length ? <div className="embrix-architecture-subsection">
        <strong>Implementation Tasks</strong>
        <ul className="embrix-context-items">{spec.tasks.map((task) => <li key={task.task_id}><strong>{task.title}</strong><small>{implementationTaskTypeLabels[task.task_type] ?? "Implementation Task"} · {implementationIntentLabels[task.implementation_intent] ?? "Implementation Intent"} · Planned</small><p>{task.description}</p></li>)}</ul>
      </div> : null}
      {spec.expected_artifacts.length ? <div className="embrix-architecture-subsection">
        <strong>Expected Outputs</strong>
        <ul className="embrix-context-items">{spec.expected_artifacts.map((artifact) => <li key={artifact.artifact_id}><strong>{artifact.name}</strong><small>{implementationArtifactTypeLabels[artifact.artifact_type] ?? "Expected Output"}</small></li>)}</ul>
      </div> : null}
      {spec.verification_refs.length ? <div className="embrix-architecture-subsection">
        <strong>Verification Reference</strong>
        <ul className="embrix-context-items">{spec.verification_refs.map((reference) => <li key={reference}><strong>{cases.get(reference) ?? "Verification Case"}</strong><small>Pending</small></li>)}</ul>
      </div> : null}
      <div className="embrix-architecture-subsection"><strong>Status</strong><p>Planned</p></div>
    </>
  </section>;
}

function capabilityLabel(value: string): string {
  return value
    .toLocaleLowerCase()
    .split("_")
    .map((part) => part.charAt(0).toLocaleUpperCase() + part.slice(1))
    .join(" ");
}

function capabilityApproval(
  evaluation: CapabilityEvaluation,
  capabilityId: string,
  required: boolean,
): string {
  if (!required) return "Not Required";
  const statuses = evaluation.approval_requirements
    .filter((item) => item.required_capabilities.includes(capabilityId))
    .map((item) => item.approval_status);
  if (statuses.includes("REJECTED")) return "Required · Rejected";
  if (statuses.includes("EXPIRED")) return "Required · Expired";
  if (statuses.includes("PENDING")) return "Required · Pending";
  if (statuses.length && statuses.every((status) => status === "APPROVED")) return "Required · Approved";
  return "Required";
}

export function EngineeringCapabilitiesPanel({ evaluation }: { evaluation: CapabilityEvaluation | null | undefined }) {
  if (!evaluation?.capabilities.length) return null;
  return <section className="embrix-engineering-capabilities" aria-label="ENGINEERING CAPABILITIES">
    <h3>ENGINEERING CAPABILITIES</h3>
    <ul className="embrix-context-items">
      {evaluation.capabilities.map((capability) => <li key={capability.capability_id}>
        <strong>{capability.name}</strong>
        <small>{capabilityLabel(capability.category)}</small>
        <p>Risk: {capabilityLabel(capability.risk_level)}</p>
        <p>Approval: {capabilityApproval(evaluation, capability.capability_id, capability.requires_approval)}</p>
        <p>Status: {capabilityLabel(capability.status)}</p>
      </li>)}
    </ul>
  </section>;
}

function authorizationRequestStatus(
  authorization: ExecutionAuthorization,
  request: ExecutionRequest,
): string {
  const policy = authorization.policies.find(
    (item) => item.capability_id === request.capability_id,
  );
  const decision = authorization.decisions.find(
    (item) => item.request_id === request.request_id,
  );
  if (
    authorization.status === "BLOCKED"
    || policy?.allowed === false
    || request.status === "DENIED"
    || request.status === "EXPIRED"
    || decision?.decision === "REJECTED"
  ) return "Blocked";
  if (
    authorization.status === "AUTHORIZED"
    && request.status === "AUTHORIZED"
    && decision?.decision === "APPROVED"
  ) return "Authorized";
  if (policy?.requires_review) return "Pending Review";
  if (policy?.requires_user_confirmation) return "Pending Approval";
  return "Pending Authorization";
}

export function ExecutionAuthorizationPanel({ authorization, evaluation, busy = false, onDecision }: { authorization: ExecutionAuthorization | null | undefined; evaluation: CapabilityEvaluation | null | undefined; busy?: boolean; onDecision?: (requestId: string, decision: "APPROVED" | "REJECTED") => void }) {
  if (!authorization?.requests.length) return null;
  const capabilityNames = new Map(
    (evaluation?.capabilities ?? []).map((item) => [item.capability_id, item.name]),
  );
  return <section className="embrix-execution-authorization" aria-label="EXECUTION AUTHORIZATION">
    <h3>EXECUTION AUTHORIZATION</h3>
    <ul className="embrix-context-items">
      {authorization.requests.map((request) => <li key={request.request_id}>
        <strong>{capabilityNames.get(request.capability_id) ?? capabilityLabel(request.capability_id)}</strong>
        <p>Risk: {capabilityLabel(request.risk_level)}</p>
        <p>Status: {authorizationRequestStatus(authorization, request)}</p>
        {request.capability_id === "FIRMWARE_CODE_GENERATION" && request.status === "REQUESTED" && authorization.decisions.find((item) => item.request_id === request.request_id)?.decision === "PENDING" && onDecision ? <div className="embrix-approval-actions">
          <button type="button" disabled={busy} onClick={() => onDecision(request.request_id, "APPROVED")}>Approve</button>
          <button type="button" disabled={busy} onClick={() => onDecision(request.request_id, "REJECTED")}>Reject</button>
        </div> : null}
      </li>)}
    </ul>
  </section>;
}

export function FirmwareSourceProposalCard({ generation, busy = false, onDecision }: { generation: FirmwareSourceGenerationState; busy?: boolean; onDecision: (decision: "APPROVED" | "REJECTED") => void }) {
  const awaiting = generation.status === "AWAITING_APPROVAL" && generation.decision.decision === "PENDING";
  return <article className="ide-pending-approval" aria-label="FIRMWARE SOURCE PROPOSAL">
    <header><strong>Firmware Source Proposal</strong><span>{awaiting ? "Awaiting Approval" : capabilityLabel(generation.status)}</span></header>
    <p>Module: {generation.proposal.module_name}</p>
    <ul>{generation.proposal.files.map((file) => <li key={file.relative_path}>{file.relative_path}</li>)}</ul>
    <p>Verification references: {generation.proposal.verification_refs.length}</p>
    <p>Firmware evidence parse: Unavailable</p>
    {awaiting ? <footer className="embrix-approval-actions">
      <button type="button" className="ide-pending-approval__allow" disabled={busy} onClick={() => onDecision("APPROVED")}>Approve Generation</button>
      <button type="button" disabled={busy} onClick={() => onDecision("REJECTED")}>Reject</button>
    </footer> : null}
  </article>;
}

export function SchematicProductionProposalCard({ production, busy = false, onDecision }: { production: SchematicProductionState; busy?: boolean; onDecision: (decision: "APPROVED" | "REJECTED") => void }) {
  const awaiting = production.status === "AWAITING_APPROVAL" && production.decision.decision === "PENDING";
  return <article className="ide-pending-approval" aria-label="SCHEMATIC PRODUCTION PROPOSAL">
    <header><strong>Schematic Production Proposal</strong><span>{awaiting ? "Awaiting Approval" : capabilityLabel(production.status)}</span></header>
    <p>Target: {production.request.target_relative_path}</p>
    <p>Components: {production.proposal.components.length} · Nets: {production.proposal.nets.length}</p>
    <p>Source: Current Design Truth</p>
    {awaiting ? <footer className="embrix-approval-actions">
      <button type="button" className="ide-pending-approval__allow" disabled={busy} onClick={() => onDecision("APPROVED")}>Approve Generation</button>
      <button type="button" disabled={busy} onClick={() => onDecision("REJECTED")}>Reject</button>
    </footer> : null}
  </article>;
}

function ProjectArtifacts({ projection }: { projection: ArtifactIntelligenceView | null | undefined }) {
  const statusLabel: Record<string, string> = {
    UNAVAILABLE: "尚未扫描",
    CANDIDATE: "待审核",
    REVIEWING: "审核中",
    APPROVED: "已确认",
    REJECTED: "已拒绝",
    SUPERSEDED: "已被新版本替代",
    INVALIDATED: "已失效",
  };
  const coverage = projection?.coverage;
  const coverageItems = coverage ? [
    ["原理图", coverage.schematic], ["PCB", coverage.pcb], ["BOM", coverage.bom],
    ["Firmware", coverage.firmware], ["Gerber", coverage.gerber], ["文档", coverage.documentation],
  ] : [];
  const hasCoverage = coverageItems.some(([, value]) => value !== "MISSING") || (coverage?.completeness_percent ?? 0) > 0;
  if (!projection || (!projection.files.length && !hasCoverage && !projection.available_evidence.length && !projection.missing_evidence.length)) return null;
  return <section className="embrix-project-artifacts" aria-label="PROJECT ARTIFACTS">
    <h3>PROJECT ARTIFACTS</h3>
    <>
      {projection.files.length ? <div className="embrix-architecture-subsection"><strong>Files</strong><ContextItems items={projection.files.map((item) => `${item.name} · ${item.status}`)} empty="" /></div> : null}
      {hasCoverage ? <div className="embrix-architecture-subsection"><strong>Coverage</strong><ContextItems items={coverageItems.map(([label, value]) => `${label}：${value}`)} empty="" /><p className="copilot-muted">工程完整度：{coverage?.completeness_percent ?? 0}%</p></div> : null}
      {projection.available_evidence.length ? <div className="embrix-architecture-subsection"><strong>Available Evidence</strong><ContextItems items={projection.available_evidence} empty="" /></div> : null}
      <div className="embrix-architecture-subsection"><strong>Evidence Review Status</strong><p>{statusLabel[projection.evidence_review_status] ?? "审核状态"}</p></div>
      {projection.missing_evidence.length ? <div className="embrix-architecture-subsection"><strong>Missing Evidence</strong><ContextItems items={projection.missing_evidence} empty="" /></div> : null}
    </>
  </section>;
}

export function AgentStatusPanel({ workflow }: { workflow: WorkflowView | null }) {
  if (!workflow?.stages.length) return null;
  return <section className="embrix-agent-status" aria-label="Embrix Status" data-agent-status-source="workflow_projection">
    <h3>Embrix Status</h3>
    <ul>
      {Object.entries(workflowLabels).map(([stage, label]) => {
        const current = workflow.stages.find((item) => item.stage === stage);
        if (!current) return null;
        const status = current.status === "finished" ? "已完成" : current.status === "ready" ? "进行中" : "尚未开始";
        const mark = status === "已完成" ? "✓" : status === "进行中" ? "·" : "○";
        return <li key={stage} data-stage={stage}><span aria-hidden="true">{mark}</span><strong>{label}</strong><small>{status}</small></li>;
      })}
    </ul>
  </section>;
}

const roadmapStatusLabels: Record<string, string> = {
  DRAFT: "草稿",
  PLANNED: "已准备",
  IN_PROGRESS: "进行中",
  BLOCKED: "已阻塞",
  COMPLETED: "已完成",
  CANCELLED: "已取消",
  PENDING: "待开始",
};

function roadmapStatus(value: string): string {
  return roadmapStatusLabels[value] ?? "待确认";
}

export function EngineeringRoadmap({ workflow }: { workflow: EngineeringWorkflow | null | undefined }) {
  const phases = workflow?.phases ?? [];
  if (!phases.length) return null;
  const completed = phases.filter((phase) => phase.status === "COMPLETED").length;
  const currentIndex = workflow?.current_phase_id ? phases.findIndex((phase) => phase.phase_id === workflow.current_phase_id) : -1;
  const current = currentIndex >= 0 ? phases[currentIndex] : phases.find((phase) => !["COMPLETED", "CANCELLED"].includes(phase.status));
  const next = current ? phases[phases.indexOf(current) + 1] : undefined;
  const nextTask = current?.tasks.find((task) => !["COMPLETED", "CANCELLED"].includes(task.status));
  const blocked = phases.filter((phase) => phase.status === "BLOCKED").map((phase) => phase.name);
  return <section className="embrix-engineering-roadmap" aria-label="ENGINEERING ROADMAP">
    <h3>ENGINEERING ROADMAP</h3>
    <>
      <dl className="ide-project-facts">
        <div><dt>Current Stage</dt><dd>{current?.name ?? "已完成"}</dd></div>
        <div><dt>Progress</dt><dd>{completed} / {phases.length}</dd></div>
        {next || nextTask ? <div><dt>Next</dt><dd>{next?.name ?? nextTask?.title}</dd></div> : null}
        <div><dt>Blocked</dt><dd>{blocked.length ? blocked.join("、") : "None"}</dd></div>
        <div><dt>Status</dt><dd>{roadmapStatus(workflow?.status ?? "PENDING")}</dd></div>
      </dl>
      <ol className="embrix-roadmap-phases">
        {phases.map((phase) => <li key={phase.phase_id} data-status={phase.status}>
          <div className="embrix-roadmap-phase-heading"><strong>{phase.name}</strong><small>{roadmapStatus(phase.status)}</small></div>
          {phase.tasks.length ? <ul>{phase.tasks.map((task) => <li key={task.task_id}><span>{task.title}</span><small>{roadmapStatus(task.status)}</small></li>)}</ul> : null}
          {phase.outputs.length ? <div className="embrix-roadmap-outputs"><strong>Outputs</strong><ul>{phase.outputs.map((output) => <li key={output.artifact_id}><span>{output.name}</span><small>{roadmapStatus(output.status)}</small></li>)}</ul></div> : null}
        </li>)}
      </ol>
    </>
  </section>;
}

export function ActivityTimeline() {
  const { events, error } = useExecutionEvents();
  const recent = events.slice(-8).reverse();
  if (!recent.length && !error) return null;
  return <section className="embrix-activity-timeline" aria-label="Agent Activity" data-activity-source="execution_event_projection">
    <h3>Activity</h3>
    {recent.length ? <ol>{recent.map((event, index) => <li key={`${event.event_id ?? event.event_type}-${index}`}><span className="embrix-activity-dot" aria-hidden="true" /><div><strong>{activityLabels[event.event_type] ?? "工程活动"}</strong>{event.status && <small>{event.status.toLocaleLowerCase().includes("fail") ? "未完成" : event.status.toLocaleLowerCase().includes("success") ? "已完成" : "处理中"}</small>}</div></li>)}</ol> : <p className="ide-list-empty">{error ? "活动暂不可用" : "暂无活动"}</p>}
  </section>;
}

function ProjectKnowledge({ model }: { model: EngineeringKnowledgeModel | null | undefined }) {
  const entities = new Map((model?.entities ?? []).map((entity) => [entity.entity_id, entity]));
  const subsystems = (model?.subsystems ?? [])
    .map((entityId) => entities.get(entityId))
    .filter((entity) => entity?.entity_type === "SUBSYSTEM")
    .map((entity) => entity?.name ?? "")
    .filter(Boolean);
  const topics = (model?.learning_topics ?? []).map((topic) => topic.title).filter(Boolean);
  const modificationPoints = model?.modification_points ?? [];
  const systemNames = (model?.system_context ?? [])
    .map((entityId) => entities.get(entityId)?.name ?? "")
    .filter(Boolean);
  if (!systemNames.length && !subsystems.length && !topics.length && !modificationPoints.length) return null;
  return <section className="embrix-project-knowledge" aria-label="PROJECT KNOWLEDGE">
    <h3>PROJECT KNOWLEDGE</h3>
    {systemNames.length ? <dl className="ide-project-facts"><div><dt>System</dt><dd>{systemNames.join("、")}</dd></div></dl> : null}
    {subsystems.length ? <div className="embrix-architecture-subsection"><strong>Subsystems</strong><ContextItems items={subsystems} empty="" /></div> : null}
    {topics.length ? <div className="embrix-architecture-subsection"><strong>Learning Topics</strong><ContextItems items={topics} empty="" /></div> : null}
    {modificationPoints.length ? <div className="embrix-architecture-subsection"><strong>Modification Points</strong><ContextItems items={modificationPoints} empty="" /></div> : null}
  </section>;
}

function LearningContext({ context }: { context: TeachingContext | null | undefined }) {
  const modeLabels: Record<string, string> = { OVERVIEW: "Overview", ENGINEERING: "Engineering", DEEP_DIVE: "Deep Dive" };
  const steps = context?.learning_path.steps ?? [];
  if (!context || (!hasText(context.subject.title) && !steps.length)) return null;
  return <section className="embrix-learning-context" aria-label="LEARNING CONTEXT">
    <h3>LEARNING CONTEXT</h3>
    <>
      <dl className="ide-project-facts">
        <div><dt>Subject</dt><dd>{context.subject.title}</dd></div>
        <div><dt>Learning Mode</dt><dd>{modeLabels[context.learning_mode] ?? context.learning_mode}</dd></div>
      </dl>
      {steps.length ? <div className="embrix-learning-path"><strong>Learning Path</strong><ol>{steps.map((step) => <li key={`${step.order}-${step.title}`}><span>{step.title}</span><small>{step.step_summary}</small></li>)}</ol></div> : null}
      <div className="embrix-learning-sources"><strong>Sources</strong><p>Confirmed Knowledge</p></div>
    </>
  </section>;
}

export function EngineeringContextPanel({ identity, project, context, projection, memoryProjection, resources, workflow, engineeringWorkflow, requirement, plan, architecture, designTruth, engineeringKnowledgeModel, teachingContext, artifactIntelligence, hardwareDesignSpec, firmwareSpec, simulationSpec, verificationSpec, implementationSpec, capabilityEvaluation, executionAuthorization, schematicProduction, authorizationDecisionBusy = false, onAuthorizationDecision, onSchematicDecision, selectedDetails = null }: { identity: ProjectWorkspaceIdentity | null; project: ProjectView | null; context: ProjectContext | null; projection: EngineeringContextProjection | null; memoryProjection: EngineeringMemoryProjection | null; resources: WorkbenchResourceNode[]; workflow: WorkflowView | null; engineeringWorkflow?: EngineeringWorkflow | null; requirement?: RequirementContext | null; plan?: EngineeringPlan | null; architecture?: ArchitectureDesign | null; designTruth?: DesignTruth | null; engineeringKnowledgeModel?: EngineeringKnowledgeModel | null; teachingContext?: TeachingContext | null; artifactIntelligence?: ArtifactIntelligenceView | null; hardwareDesignSpec?: HardwareDesignSpecification | null; firmwareSpec?: FirmwareSpecification | null; simulationSpec?: SimulationSpecification | null; verificationSpec?: VerificationSpecification | null; implementationSpec?: ImplementationSpecification | null; capabilityEvaluation?: CapabilityEvaluation | null; executionAuthorization?: ExecutionAuthorization | null; schematicProduction?: SchematicProductionState | null; authorizationDecisionBusy?: boolean; onAuthorizationDecision?: (requestId: string, decision: "APPROVED" | "REJECTED") => void; onSchematicDecision?: (decision: "APPROVED" | "REJECTED") => void; selectedDetails?: ReactNode }) {
  const memory = normalizeEngineeringMemoryProjection(identity?.id ?? null, memoryProjection);
  const hardware = [...resourceLabels(resources, "HARDWARE"), ...referenceLabels(context, "HARDWARE")].filter((value, index, all) => all.indexOf(value) === index);
  const firmware = [...resourceLabels(resources, "FIRMWARE"), ...referenceLabels(context, "FIRMWARE")].filter((value, index, all) => all.indexOf(value) === index);
  const documents = [...resourceLabels(resources, "DOCUMENT"), ...referenceLabels(context, "DOCUMENT")].filter((value, index, all) => all.indexOf(value) === index);
  const hasContextProjection = Boolean(
    hardware.length || firmware.length || documents.length
      || context?.groups.some((group) => group.references.length)
      || context?.evidence_sources.some((item) => item.count > 0)
      || projection?.references.some((item) => item.count > 0)
      || projection?.evidenceSources.some((item) => item.count > 0)
      || projection?.resources.some((item) => item.status !== "UNAVAILABLE")
      || memory.items.length,
  );
  const hasKnowledge = Boolean(
    engineeringKnowledgeModel?.system_context.length
      || engineeringKnowledgeModel?.subsystems.length
      || engineeringKnowledgeModel?.entities.length
      || engineeringKnowledgeModel?.learning_topics.length
      || engineeringKnowledgeModel?.modification_points.length,
  );
  const hasArtifact = Boolean(
    artifactIntelligence?.files.length
      || artifactIntelligence?.available_evidence.length
      || artifactIntelligence?.missing_evidence.length
      || (artifactIntelligence?.coverage?.completeness_percent ?? 0) > 0
      || Object.values(artifactIntelligence?.coverage ?? {}).some((value) => value !== "MISSING" && value !== 0),
  );
  const hasStructuredContext = Boolean(
    hasContextProjection || hasKnowledge || hasArtifact || teachingContext?.subject.title.trim()
      || teachingContext?.learning_path.steps.length || engineeringWorkflow?.phases.length
      || workflow?.stages.length || selectedDetails || schematicProduction
      || requirement && (requirement.requirements.length || requirement.constraints.length || requirement.proposed_items.length || requirement.open_questions.length || requirement.acceptance_criteria?.length || requirement.conflicts?.length)
      || plan && (hasText(plan.title) || plan.phases.length || plan.verification_strategy.length)
      || architecture && (hasText(architecture.title) || hasText(architecture.objective) || architecture.system_components.length || architecture.interfaces.length || architecture.decisions.length || architecture.constraints.length)
      || designTruth && (designTruth.components.length || designTruth.interfaces.length || designTruth.signals.length || designTruth.pins.length || designTruth.peripherals.length || designTruth.nets.length || designTruth.power_domains.length || designTruth.firmware_bindings.length)
      || hardwareDesignSpec && (hardwareDesignSpec.hardware_domains.length || hardwareDesignSpec.interfaces.length || hardwareDesignSpec.decisions.length)
      || firmwareSpec && (firmwareSpec.modules.length || firmwareSpec.bindings.length || firmwareSpec.verification_requirements.length)
      || simulationSpec && (simulationSpec.models.length || simulationSpec.targets.length || simulationSpec.scenarios.length || simulationSpec.evidence_requirements.length)
      || verificationSpec && (verificationSpec.cases.length || verificationSpec.evidence_requirements.length)
      || implementationSpec && (implementationSpec.tasks.length || implementationSpec.expected_artifacts.length || implementationSpec.verification_refs.length)
      || capabilityEvaluation?.capabilities.length || executionAuthorization?.requests.length,
  );
  return <aside className="embrix-engineering-context" aria-label="Engineering Context" data-context-source="project_context_projection">
    <Panel title="Engineering Context" className="embrix-context-panel">
      <section><h3>Project</h3>{identity ? <dl className="ide-project-facts"><div><dt>当前项目</dt><dd>{identity.name}</dd></div><div><dt>平台</dt><dd>{identityPlatform(identity)}</dd></div><div><dt>工程路径</dt><dd>{project?.relative_path ?? "尚未提供"}</dd></div><div><dt>加载状态</dt><dd>{identity.lifecycle === "ready" ? "已加载" : "初始化中"}</dd></div></dl> : <p className="ide-list-empty">未选择项目</p>}</section>
      {hasStructuredContext ? <>
        <CurrentRequirement requirement={requirement} />
        <CurrentPlan plan={plan} />
        <CurrentArchitecture architecture={architecture} />
        <CurrentDesignTruth designTruth={designTruth} />
        <EngineeringRoadmap workflow={engineeringWorkflow} />
        <HardwareDesignSpecificationPanel spec={hardwareDesignSpec} />
        <FirmwareDesignSpecificationPanel spec={firmwareSpec} hardwareSpec={hardwareDesignSpec} />
        <SimulationDesignSpecificationPanel spec={simulationSpec} />
        <VerificationSpecificationPanel spec={verificationSpec} />
        <ImplementationSpecificationPanel spec={implementationSpec} verificationSpec={verificationSpec} />
        <EngineeringCapabilitiesPanel evaluation={capabilityEvaluation} />
        <ExecutionAuthorizationPanel authorization={executionAuthorization} evaluation={capabilityEvaluation} busy={authorizationDecisionBusy} onDecision={onAuthorizationDecision} />
        {schematicProduction ? <SchematicProductionProposalCard production={schematicProduction} busy={authorizationDecisionBusy} onDecision={onSchematicDecision ?? (() => undefined)} /> : null}
        <ProjectKnowledge model={engineeringKnowledgeModel} />
        <LearningContext context={teachingContext} />
        <ProjectArtifacts projection={artifactIntelligence} />
        {hardware.length ? <section data-context-domain="hardware"><h3>Hardware</h3><ContextItems items={hardware} empty="" /></section> : null}
        {firmware.length ? <section data-context-domain="firmware"><h3>Firmware</h3><ContextItems items={firmware} empty="" /></section> : null}
        {documents.length ? <section data-context-domain="documents"><h3>Documents</h3><ContextItems items={documents} empty="" /></section> : null}
        {projection?.evidenceSources.some((item) => item.count > 0) ? <section data-context-domain="evidence"><h3>Evidence</h3>{projection.evidenceSources.filter((item) => item.count > 0).map((item) => <ContextStatus key={item.source} label={item.source} value={`${item.count} 条`} tone="primary" />)}</section> : null}
        {memory.items.length ? <section data-context-domain="memory"><h3>Memory</h3><div className="embrix-context-memory">{memory.items.slice(0, 4).map((item) => <article key={item.id}><strong>{item.title}</strong><p>{item.summary}</p><small>{memorySourceLabel(item.source)} · {formatMemoryDate(item.createdAt)}</small></article>)}</div></section> : null}
        <AgentStatusPanel workflow={workflow} />
        <ActivityTimeline />
      </> : <section className="embrix-context-empty" data-engineering-context-empty="true" aria-label="Engineering Context empty state"><h3>Engineering Context</h3><p>尚未建立工程上下文。完成需求沟通后，这里将显示当前设计、关键工程决策和验证状态。</p></section>}
      {selectedDetails && <section className="embrix-context-inspector" aria-label="选中工程对象">{selectedDetails}</section>}
    </Panel>
  </aside>;
}
