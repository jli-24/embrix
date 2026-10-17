import { WORKFLOW_STAGE_TITLES, WORKFLOW_STAGES } from "./engineeringWorkflowModel";
import type { WorkflowStageView, WorkflowView } from "./engineeringWorkflowModel";

function stageMark(stage: WorkflowStageView): string {
  if (stage.status === "finished") return "✓";
  if (stage.status === "ready") return "·";
  return "—";
}

export function EngineeringWorkflow({ workflow, showTaskLabel = true }: { workflow: WorkflowView | null; showTaskLabel?: boolean }) {
  if (!workflow) {
    return <section className="ide-workflow ide-workflow--empty" aria-label="工程流程">
      <h2>工程任务</h2>
      <p>暂无工程流程</p>
    </section>;
  }

  return <section className="ide-workflow" aria-label="工程流程" data-workflow-status={workflow.status}>
    <header className="ide-workflow__header">
      {showTaskLabel && <p>工程任务</p>}
      <h2>{workflow.taskTitle}</h2>
      <h3>工程阶段</h3>
    </header>
    <ol className="ide-workflow__stages">
      {WORKFLOW_STAGES.map((stageKey) => {
        const stage = workflow.stages.find((candidate) => candidate.stage === stageKey) ?? {
          stage: stageKey,
          title: WORKFLOW_STAGE_TITLES[stageKey],
          status: "pending" as const,
        };
        return <li key={stage.stage} data-stage={stage.stage} data-status={stage.status}>
        <span className="ide-workflow__mark" aria-hidden="true">{stageMark(stage)}</span>
        <div>
          <strong>{stage.title}</strong>
          {stage.description && <p>{stage.description}</p>}
          {stage.relatedCount !== undefined && stage.relatedCount > 0 && <p className="ide-workflow__count">{stage.relatedCount} 条关联记录</p>}
        </div>
        </li>;
      })}
    </ol>
  </section>;
}
