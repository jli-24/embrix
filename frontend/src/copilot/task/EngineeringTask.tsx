import type { EngineeringTaskProjection } from "./engineeringTaskModel";

export function EngineeringTask({ task }: { task: EngineeringTaskProjection | null }) {
  if (!task) return null;
  return <section className="ide-task" aria-label="当前话题" data-task-status={task.identity.status} data-task-presentation="conversation-context">
    <header className="ide-task__header">
      <p>当前话题</p>
      <h2>{task.identity.title}</h2>
    </header>
  </section>;
}
