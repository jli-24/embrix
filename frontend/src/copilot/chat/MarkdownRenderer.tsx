import { createElement, type ReactNode } from "react";

import type { ConversationMode } from "./conversationRenderer";
import { sanitizeAssistantMarkdown } from "./conversationRenderer";

type MarkdownRendererProps = {
  content: string;
  mode: ConversationMode;
  renderer?: "chat-markdown" | "document" | "artifact";
};

type CodeBlockProps = {
  language: string;
  code: string;
};

const CODE_LANGUAGE_ALIASES: Record<string, string> = {
  c: "c", cc: "cpp", cpp: "cpp", cxx: "cpp", h: "cpp", hh: "cpp", hpp: "cpp", "c++": "cpp",
  python: "python", py: "python",
  bash: "shell", sh: "shell", shell: "shell", zsh: "shell", ps1: "shell", powershell: "shell",
  json: "json", jsonc: "json", yaml: "yaml", yml: "yaml", toml: "toml",
  config: "config", conf: "config", cfg: "config", ini: "config", env: "config",
};

function normalizeCodeLanguage(language: string): string | null {
  return CODE_LANGUAGE_ALIASES[language.trim().toLocaleLowerCase()] ?? null;
}

const blockStartPattern = /^(?:#{1,6}\s+|>\s?|[-*+]\s+|\d+[.)]\s+|```|\s*\|)/u;

function splitTableRow(line: string): string[] {
  const trimmed = line.trim().replace(/^\|/u, "").replace(/\|$/u, "");
  return trimmed.split(/(?<!\\)\|/u).map((cell) => cell.trim().replace(/\\\|/gu, "|"));
}

function isTableSeparator(line: string): boolean {
  const cells = splitTableRow(line);
  return cells.length >= 2 && cells.every((cell) => /^:?-{3,}:?$/u.test(cell));
}

function safeHref(value: string): string | null {
  if (!/^https?:\/\//iu.test(value)) return null;
  try {
    const url = new URL(value);
    return url.protocol === "http:" || url.protocol === "https:" ? value : null;
  } catch {
    return null;
  }
}

function renderInline(value: string): ReactNode[] {
  const pattern = /(`[^`\n]+`|\[[^\]\n]+\]\([^\)\n]+\)|\*\*[^*\n]+\*\*|__[^_\n]+__|(?<!\*)\*[^*\n]+\*(?!\*)|(?<!_)_[^_\n]+_(?!_))/gu;
  const nodes: ReactNode[] = [];
  let cursor = 0;
  let match: RegExpExecArray | null;
  let index = 0;
  while ((match = pattern.exec(value)) !== null) {
    if (match.index > cursor) nodes.push(value.slice(cursor, match.index));
    const token = match[0];
    if (token.startsWith("`")) {
      nodes.push(<code key={`inline-code-${index}`}>{token.slice(1, -1)}</code>);
    } else if (token.startsWith("[")) {
      const link = /^\[([^\]]+)\]\(([^)]+)\)$/u.exec(token);
      const href = link ? safeHref(link[2]) : null;
      nodes.push(href ? <a key={`link-${index}`} href={href} target="_blank" rel="noreferrer">{link?.[1]}</a> : link?.[1] ?? token);
    } else if (token.startsWith("**") || token.startsWith("__")) {
      nodes.push(<strong key={`strong-${index}`}>{token.slice(2, -2)}</strong>);
    } else {
      nodes.push(<em key={`emphasis-${index}`}>{token.slice(1, -1)}</em>);
    }
    cursor = match.index + token.length;
    index += 1;
  }
  if (cursor < value.length) nodes.push(value.slice(cursor));
  return nodes;
}

function CodeBlock({ language, code }: CodeBlockProps) {
  return <div className="ide-markdown-code" data-language={language} style={{ userSelect: "text" }}>
    <pre style={{ overflowX: "auto", padding: "12px", userSelect: "text", whiteSpace: "pre-wrap" }}><code>{code}</code></pre>
  </div>;
}

function PlainFenceText({ code }: { code: string }) {
  return <div className="ide-markdown-plain-fence" data-fence-renderer="plain-text" style={{ userSelect: "text", whiteSpace: "pre-wrap" }}>{code}</div>;
}

function isUnorderedList(line: string): boolean {
  return /^\s*[-*+]\s+/u.test(line);
}

function isOrderedList(line: string): boolean {
  return /^\s*\d+[.)]\s+/u.test(line);
}

function isBlockStart(line: string): boolean {
  return blockStartPattern.test(line) || isTableSeparator(line);
}

function renderBlocks(lines: string[], keyPrefix: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  let index = 0;
  let blockIndex = 0;
  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) {
      index += 1;
      continue;
    }

    const fence = /^\s*```\s*([\w-]*)\s*$/u.exec(line);
    if (fence) {
      const codeLines: string[] = [];
      index += 1;
      while (index < lines.length && !/^\s*```\s*$/u.test(lines[index])) {
        codeLines.push(lines[index]);
        index += 1;
      }
      if (index < lines.length) index += 1;
      const code = codeLines.join("\n");
      const language = normalizeCodeLanguage(fence[1]);
      nodes.push(language
        ? <CodeBlock key={`${keyPrefix}-code-${blockIndex}`} language={language} code={code} />
        : <PlainFenceText key={`${keyPrefix}-plain-fence-${blockIndex}`} code={code} />);
      blockIndex += 1;
      continue;
    }

    const heading = /^(#{1,6})\s+(.+)$/u.exec(line);
    if (heading) {
      const level = heading[1].length;
      const headingTag = `h${level}` as "h1" | "h2" | "h3" | "h4" | "h5" | "h6";
      nodes.push(createElement(headingTag, { key: `${keyPrefix}-heading-${blockIndex}` }, renderInline(heading[2])));
      index += 1;
      blockIndex += 1;
      continue;
    }

    if (index + 1 < lines.length && line.includes("|") && isTableSeparator(lines[index + 1])) {
      const headers = splitTableRow(line);
      const rows: string[][] = [];
      index += 2;
      while (index < lines.length && lines[index].includes("|") && lines[index].trim()) {
        rows.push(splitTableRow(lines[index]));
        index += 1;
      }
      nodes.push(<table key={`${keyPrefix}-table-${blockIndex}`}><thead><tr>{headers.map((cell, cellIndex) => <th key={`header-${cellIndex}`}>{renderInline(cell)}</th>)}</tr></thead><tbody>{rows.map((row, rowIndex) => <tr key={`row-${rowIndex}`}>{headers.map((_, cellIndex) => <td key={`cell-${rowIndex}-${cellIndex}`}>{renderInline(row[cellIndex] ?? "")}</td>)}</tr>)}</tbody></table>);
      blockIndex += 1;
      continue;
    }

    if (/^\s*>/u.test(line)) {
      const quoteLines: string[] = [];
      while (index < lines.length && /^\s*>/u.test(lines[index])) {
        quoteLines.push(lines[index].replace(/^\s*>\s?/u, ""));
        index += 1;
      }
      nodes.push(<blockquote key={`${keyPrefix}-quote-${blockIndex}`}>{renderBlocks(quoteLines, `${keyPrefix}-quote-${blockIndex}`)}</blockquote>);
      blockIndex += 1;
      continue;
    }

    if (isUnorderedList(line) || isOrderedList(line)) {
      const ordered = isOrderedList(line);
      const items: string[] = [];
      while (index < lines.length && (ordered ? isOrderedList(lines[index]) : isUnorderedList(lines[index]))) {
        items.push(lines[index].replace(ordered ? /^\s*\d+[.)]\s+/u : /^\s*[-*+]\s+/u, ""));
        index += 1;
      }
      const List = ordered ? "ol" : "ul";
      nodes.push(<List key={`${keyPrefix}-list-${blockIndex}`}>{items.map((item, itemIndex) => <li key={`item-${itemIndex}`}>{renderInline(item)}</li>)}</List>);
      blockIndex += 1;
      continue;
    }

    if (/^\s*(?:---+|\*\s*\*\s*\*)\s*$/u.test(line)) {
      nodes.push(<hr key={`${keyPrefix}-rule-${blockIndex}`} />);
      index += 1;
      blockIndex += 1;
      continue;
    }

    const paragraphLines = [line];
    index += 1;
    while (index < lines.length && lines[index].trim() && !isBlockStart(lines[index])) {
      paragraphLines.push(lines[index]);
      index += 1;
    }
    const paragraphContent: ReactNode[] = [];
    paragraphLines.forEach((part, partIndex) => {
      if (partIndex > 0) paragraphContent.push(<br key={`break-${partIndex}`} />);
      paragraphContent.push(...renderInline(part));
    });
    nodes.push(createElement("p", { key: `${keyPrefix}-paragraph-${blockIndex}` }, paragraphContent));
    blockIndex += 1;
  }
  return nodes;
}

export function MarkdownRenderer({ content, mode, renderer = "chat-markdown" }: MarkdownRendererProps) {
  const sanitized = sanitizeAssistantMarkdown(content, mode);
  return <div className={`ide-markdown-body ide-markdown-body--${renderer}`} data-renderer={renderer} style={{ userSelect: "text" }}>{renderBlocks(sanitized.split(/\r?\n/u), renderer)}</div>;
}
