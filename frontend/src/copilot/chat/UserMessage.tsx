import { Message } from "../../design-system/components";

function formatMessageTime(value: string | null | undefined): string | null {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  const pad = (part: number) => String(part).padStart(2, "0");
  return `${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function UserMessage({ question, createdAt = null }: { question: string; createdAt?: string | null }) {
  const time = formatMessageTime(createdAt);
  return <Message variant="user" label=""><p>{question}</p>{time && <time className="ide-user-time" dateTime={createdAt ?? undefined}>{time}</time>}</Message>;
}
