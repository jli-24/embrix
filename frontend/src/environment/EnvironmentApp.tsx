import { useEffect, useState } from "react";

import {
  type AuditView,
  type Collaboration,
  type EnvironmentProject,
  type MemoryView,
  type RuntimeHealth,
  EnvironmentRequestError,
  fetchAudit,
  fetchMemoryViews,
  fetchProjects,
  fetchRuntimeHealth,
  fetchTeam,
} from "./api";

export default function EnvironmentApp() {
  const [projects, setProjects] = useState<EnvironmentProject[]>([]);
  const [team, setTeam] = useState<Collaboration | null>(null);
  const [health, setHealth] = useState<RuntimeHealth[]>([]);
  const [audit, setAudit] = useState<AuditView[]>([]);
  const [memory, setMemory] = useState<MemoryView[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void Promise.all([fetchProjects(), fetchTeam(), fetchRuntimeHealth(), fetchAudit(), fetchMemoryViews()])
      .then(([nextProjects, nextTeam, nextHealth, nextAudit, nextMemory]) => {
        setProjects(nextProjects);
        setTeam(nextTeam);
        setHealth(nextHealth);
        setAudit(nextAudit);
        setMemory(nextMemory);
      })
      .catch((reason) => setError(reason instanceof EnvironmentRequestError ? reason.code : "ENVIRONMENT_UNAVAILABLE"));
  }, []);

  return <main className="environment-view"><header><p className="eyebrow">Embedded Copilot</p><h1>Engineering Environment</h1><p>Read-only operating environment</p></header>{error && <p className="environment-error" role="alert">{error}</p>}<section className="environment-grid"><section><h2>Projects</h2>{projects.map((item) => <article key={item.project.project_id}><strong>{item.project.project_name_reference}</strong><span>{item.project.status}</span><small>{item.project.workspace_reference}</small></article>)}</section><section><h2>Team Activity</h2><p>{team?.team.team_id ?? "Unavailable"}</p>{team?.recent_activity_references.map((item) => <small key={item}>{item}</small>)}</section><section><h2>Runtime Health</h2>{health.map((item) => <p key={item.component}>{item.component}: <strong>{item.status}</strong></p>)}</section><section><h2>Audit Timeline</h2>{audit.map((item) => <article key={`${item.object_reference}-${item.timestamp}`}><strong>{item.event_type}</strong><span>{item.status}</span><small>{item.object_reference}</small></article>)}</section><section><h2>Memory References</h2>{memory.map((item) => <p key={item.memory_reference}>{item.memory_reference}: {item.status}</p>)}</section></section></main>;
}
