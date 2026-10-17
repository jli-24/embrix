import type { EngineeringTaskProjection } from "./engineeringTaskModel";

export function TaskContinuityAnswer({ task }: { task: EngineeringTaskProjection }) {
  return <section className="ide-task-answer" aria-label="工程任务摘要">
    <section className="ide-document-section"><h3>任务摘要</h3><p>已根据当前工程上下文整理。</p></section>
    <section className="ide-document-section" aria-label="证据"><h3>证据</h3>{task.evidenceCount && task.evidenceCount > 0 ? <p>{task.evidenceCount} references</p> : <p className="ide-compact-empty">尚未产生工程证据</p>}</section>
    <section className="ide-document-section" aria-label="建议"><h3>建议</h3><p>生成只读工程建议</p></section>
  </section>;
}
