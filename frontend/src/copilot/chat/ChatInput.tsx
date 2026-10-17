import { ArrowUp, FileText, Plus, X } from "lucide-react";
import { type FormEvent, useRef, useState } from "react";
import type { ContextReference } from "../api/chatClient";
import { shortFingerprint } from "../viewModels";

export function insertContextReference(value: string, context: string): string {
  if (value.endsWith("@")) return `${value.slice(0, -1)}@${context} `;
  return `${value}${value && !/\s$/.test(value) ? " " : ""}@${context} `;
}

function isAttachment(reference: ContextReference): boolean {
  return reference.reference_id.startsWith("attachment-");
}

type ChatInputProps = {
  connected: boolean;
  pending: boolean;
  selected: ContextReference[];
  onToggleReference(reference: ContextReference): void;
  onAttach(file: File): void;
  onSubmit(question: string): void;
  /** Compatibility-only props from the previous composer contract are ignored. */
  [key: string]: unknown;
};

export function ChatInput({ connected, pending, selected, onToggleReference, onAttach, onSubmit }: ChatInputProps) {
  const [question, setQuestion] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const attachments = selected.filter(isAttachment);
  const composerUnavailable = !connected;

  function resizeTextarea(element = textareaRef.current) {
    if (!element) return;
    element.style.height = "auto";
    element.style.height = `${Math.min(element.scrollHeight, 104)}px`;
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    const value = question.trim();
    if (!value || !connected || pending) return;
    onSubmit(value);
    setQuestion("");
    if (textareaRef.current) textareaRef.current.style.height = "28px";
  }

  return <form className={`ide-composer ${attachments.length ? "ide-composer--with-attachments" : ""}`} data-composer-layout="conversation-rail" data-composer-footer="in-flow" data-composer-overflow="visible" data-composer-controls="attach-send-only" data-composer-reference-entry="removed" data-composer-research-control="removed" data-enter-send-hint="hidden" data-composer-radius="24px" data-composer-shadow="subtle" data-composer-state={composerUnavailable ? "unavailable" : "ready"} data-composer-unavailable-reason={composerUnavailable ? "PROJECT_OR_CONFIGURATION_REQUIRED" : undefined} onSubmit={submit}>
    <div className="ide-composer__box">
      {attachments.length > 0 && <section className="ide-composer__reference-tray ide-attachment-tray" aria-label="附件引用">{attachments.map((reference) => <article key={reference.fingerprint}><FileText size={15} aria-hidden="true" /><div><strong>{reference.display_label}</strong><span>附件引用</span><code>SHA-256 {shortFingerprint(reference.source_fingerprint)}</code></div><button type="button" aria-label={`移除 ${reference.display_label} 附件引用`} disabled={!connected || pending} onClick={() => onToggleReference(reference)}><X size={14} /></button></article>)}</section>}
      <div className="ide-composer__editor"><textarea ref={textareaRef} value={question} onChange={(event) => { setQuestion(event.target.value); resizeTextarea(event.currentTarget); }} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); event.currentTarget.form?.requestSubmit(); } }} placeholder={composerUnavailable ? "请先选择 Project 或完成环境配置" : ""} aria-label="工程问题" rows={1} disabled={!connected || pending} /></div>
      <div className="ide-composer__footer ide-composer__toolbar"><div className="ide-composer__footer-actions"><input ref={fileRef} className="ide-visually-hidden" type="file" tabIndex={-1} disabled={!connected || pending} onChange={(event) => { const file = event.currentTarget.files?.[0]; if (file) onAttach(file); event.currentTarget.value = ""; }} /><button className="ide-composer-icon" type="button" title="添加附件" aria-label="添加附件" data-composer-plus="icon-only" disabled={!connected || pending} onClick={() => fileRef.current?.click()}><Plus size={17} /></button></div><button className="ide-send-button" type="submit" title="发送工程问题" aria-label="发送工程问题" data-send-state={question.trim() ? "active" : "empty"} data-send-shape="circle" disabled={!connected || pending || !question.trim()}><ArrowUp size={19} strokeWidth={2} aria-hidden="true" /></button></div>
    </div>
  </form>;
}
