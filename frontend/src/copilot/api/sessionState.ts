import { useSyncExternalStore } from "react";

export const DESKTOP_SESSION_EXPIRED = "DESKTOP_SESSION_EXPIRED";

export type DesktopSessionState = "ACTIVE" | "EXPIRED";

let state: DesktopSessionState = "ACTIVE";
const listeners = new Set<() => void>();

function notify(): void {
  listeners.forEach((listener) => listener());
}

export function getDesktopSessionState(): DesktopSessionState {
  return state;
}

export function subscribeDesktopSession(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function markDesktopSessionActive(): void {
  if (state === "ACTIVE") return;
  state = "ACTIVE";
  notify();
}

export function markDesktopSessionExpired(): void {
  if (state === "EXPIRED") return;
  state = "EXPIRED";
  notify();
}

export function isDesktopSessionExpired(): boolean {
  return state === "EXPIRED";
}

export function readDesktopSessionErrorCode(payload: unknown): string | null {
  if (!payload || typeof payload !== "object") return null;
  const value = payload as { error?: unknown; detail?: unknown };
  if (typeof value.error === "string") return value.error;
  if (typeof value.detail === "string") return value.detail;
  return null;
}

export function mapDesktopSessionError(status: number, code: string | null | undefined): string | null {
  if (status === 401 && code === DESKTOP_SESSION_EXPIRED) markDesktopSessionExpired();
  return code ?? null;
}

export function useDesktopSessionState(): DesktopSessionState {
  return useSyncExternalStore(subscribeDesktopSession, getDesktopSessionState, getDesktopSessionState);
}
