import { FormEvent, useEffect, useState } from "react";

import {
  askDesktop, DesktopRequestError, Explorer, fetchExplorer, fetchNotifications,
  fetchPreferences, fetchTimeline, Notification, openDesktopSession, Preferences,
  ProductSession, savePreferences, Timeline,
} from "./api";
import { WorkspaceApplyPanel } from "../components/workspace/WorkspaceApplyPanel";

const requester = "desktop-user";
const eventTypes = ["SESSION_OPENED", "SESSION_CLOSED", "SESSION_EXPIRED", "WORKSPACE_REFRESHED", "CHAT_COMPLETED", "CHAT_REJECTED", "CHAT_FAILED", "PREFERENCES_UPDATED"];

export default function DesktopApp() {
  const [projectId, setProjectId] = useState("");
  const [session, setSession] = useState<ProductSession | null>(null);
  const [explorer, setExplorer] = useState<Explorer | null>(null);
  const [timeline, setTimeline] = useState<Timeline | null>(null);
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [preferences, setPreferences] = useState<Preferences | null>(null);
  const [question, setQuestion] = useState("");
  const [changesetId, setChangesetId] = useState("");
  const [answer, setAnswer] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { void fetchPreferences(requester).then(setPreferences).catch(() => setError("PREFERENCES_UNAVAILABLE")); }, []);
  useEffect(() => {
    if (!session) return;
    const load = () => Promise.all([fetchExplorer(requester, session.product_session_id), fetchTimeline(requester, session.product_session_id), fetchNotifications(requester, session.product_session_id)]).then(([nextExplorer, nextTimeline, nextNotifications]) => { setExplorer(nextExplorer); setTimeline(nextTimeline); setNotifications(nextNotifications); });
    void load().catch(() => setError("DESKTOP_PRODUCT_UNAVAILABLE"));
    const stream = new EventSource(`/api/v1/desktop/sessions/${session.product_session_id}/events?requester=${encodeURIComponent(requester)}`);
    eventTypes.forEach((eventType) => stream.addEventListener(eventType, () => { void load().catch(() => setError("DESKTOP_PRODUCT_UNAVAILABLE")); }));
    stream.addEventListener("RESYNC_REQUIRED", () => { void load().catch(() => setError("DESKTOP_PRODUCT_UNAVAILABLE")); });
    return () => stream.close();
  }, [session]);

  async function open(event: FormEvent) { event.preventDefault(); setError(null); setAnswer(null); try { setSession(await openDesktopSession(requester, projectId)); } catch (reason) { setError(reason instanceof DesktopRequestError ? reason.code : "DESKTOP_PRODUCT_UNAVAILABLE"); } }
  async function chat(event: FormEvent) { event.preventDefault(); if (!session || !question.trim()) return; setError(null); try { const response = await askDesktop(requester, session.product_session_id, question); setAnswer(response.display.content); setQuestion(""); } catch (reason) { setError(reason instanceof DesktopRequestError ? reason.code : "CHAT_UNAVAILABLE"); } }
  async function updateTheme() { if (!preferences) return; try { setPreferences(await savePreferences(requester, { ...preferences, theme: preferences.theme === "DARK" ? "LIGHT" : "DARK", updated_at: new Date().toISOString() })); } catch { setError("PREFERENCES_UNAVAILABLE"); } }

  return <main className="desktop-product"><header><p className="eyebrow">Embedded Copilot</p><h1>Engineering Desktop</h1><p>Read-only project intelligence</p></header><form onSubmit={open}><input value={projectId} onChange={(event) => setProjectId(event.target.value)} placeholder="Project ID" maxLength={96} /><button disabled={!projectId.trim()}>Open project</button></form>{error && <p role="alert">{error}</p>}{session && <section className="desktop-grid"><aside><h2>Project Explorer</h2>{explorer?.items.map((item) => <p key={item.reference.reference_id}>{item.reference.source_domain} / {item.reference.source_kind} / {item.reference.reference_id}</p>)}<label>ChangeSet ID<input value={changesetId} onChange={(event) => setChangesetId(event.target.value)} /></label></aside><section><h2>Timeline</h2>{timeline?.entries.map((item) => <p key={item.reference_id}>{item.source_domain}: {item.reference_id} ({item.status ?? ""})</p>)}<h2>Chat</h2><form onSubmit={chat}><textarea value={question} onChange={(event) => setQuestion(event.target.value)} placeholder="Ask an engineering question" /><button disabled={!question.trim()}>Ask</button></form>{answer && <article className="desktop-answer">{answer}</article>}<WorkspaceApplyPanel projectId={projectId || null} changesetId={changesetId || null} /></section><aside><h2>Notifications</h2>{notifications.map((item) => <p key={item.notification_id}>{item.event_type}</p>)}<h2>Display</h2><button onClick={updateTheme}>{preferences?.theme ?? "SYSTEM"}</button></aside></section>}</main>;
}
