import { MarkdownRenderer } from "./MarkdownRenderer";

export function ArtifactRenderer({ content, artifactRefs }: { content: string; artifactRefs: string[] }) {
  return <div className="ide-artifact-message" data-renderer="artifact" data-artifact-ref-count={artifactRefs.length}>
    <MarkdownRenderer content={content} mode="EXPLORATION" renderer="artifact" />
    {artifactRefs.length > 0 && <div className="ide-artifact-message__refs" aria-label="关联工程产物">{artifactRefs.map((ref) => <code key={ref}>{ref}</code>)}</div>}
  </div>;
}
