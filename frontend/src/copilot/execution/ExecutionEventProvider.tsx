import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { streamExecutionEvents, type ExecutionOperationEvent } from "../api/executionClient";

type ExecutionEventContextValue = { events: ExecutionOperationEvent[]; cursor: string | null; error: string | null };
const ExecutionEventContext = createContext<ExecutionEventContextValue>({ events: [], cursor: null, error: null });

export function ExecutionEventProvider({ projectId, enabled, children }: { projectId: string | null; enabled: boolean; children: ReactNode }) {
  const [events, setEvents] = useState<ExecutionOperationEvent[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setEvents([]); setCursor(null); setError(null);
    if (!enabled || !projectId) return undefined;
    const controller = new AbortController();
    let alive = true;
    const consume = async () => {
      let lastEventId: string | null = null;
      while (alive) {
        let resync = false;
        try {
          const next = await streamExecutionEvents(projectId, lastEventId, (event) => {
            if (!alive) return;
            if (event.event_type === "RESYNC_REQUIRED") { resync = true; lastEventId = null; setEvents([]); setCursor(null); setError("EXECUTION_RESYNC_REQUIRED"); return; }
            setEvents((current) => [...current, event].slice(-200));
            if (event.event_id) { lastEventId = event.event_id; setCursor(event.event_id); }
          }, fetch, controller.signal);
          if (resync) lastEventId = null;
          else if (next) { lastEventId = next; setCursor(next); }
          if (alive) await new Promise((resolve) => window.setTimeout(resolve, 250));
        } catch (reason) {
          if (!alive || (reason as { name?: string }).name === "AbortError") break;
          setError(reason instanceof Error ? reason.message : "EXECUTION_EVENT_STREAM_REJECTED");
          await new Promise((resolve) => window.setTimeout(resolve, 1000));
        }
      }
    };
    void consume();
    return () => { alive = false; controller.abort(); };
  }, [enabled, projectId]);
  const value = useMemo(() => ({ events, cursor, error }), [cursor, error, events]);
  return <ExecutionEventContext.Provider value={value}>{children}</ExecutionEventContext.Provider>;
}

export function useExecutionEvents() { return useContext(ExecutionEventContext); }
