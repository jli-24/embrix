import { MarkdownRenderer } from "./MarkdownRenderer";

export function DocumentRenderer({ content }: { content: string }) {
  return <div className="ide-document-renderer" data-renderer="document">
    <MarkdownRenderer content={content} mode="DOCUMENT" renderer="document" />
  </div>;
}
