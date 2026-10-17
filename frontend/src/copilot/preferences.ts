export type UiTheme = "light" | "dark" | "system";
export type ResolvedUiTheme = "light" | "dark";
export type UiDensity = "compact" | "comfortable";
export type AnswerDetail = "concise" | "standard" | "detailed";
export type EvidenceDisplay = "expanded" | "compact";

export type UiPreferences = {
  theme: UiTheme;
  density: UiDensity;
  answerDetail: AnswerDetail;
  evidenceDisplay: EvidenceDisplay;
};

export const UI_PREFERENCES_KEY = "embedded-copilot.ui.preferences.v1";
export const DEFAULT_UI_PREFERENCES: UiPreferences = { theme: "light", density: "compact", answerDetail: "standard", evidenceDisplay: "expanded" };

export function readUiPreferences(storage: Pick<Storage, "getItem"> | null = typeof window === "undefined" ? null : window.localStorage): UiPreferences {
  if (!storage) return DEFAULT_UI_PREFERENCES;
  try {
    const value = JSON.parse(storage.getItem(UI_PREFERENCES_KEY) ?? "null") as Partial<UiPreferences> | null;
    if (!value) return DEFAULT_UI_PREFERENCES;
    return {
      theme: value.theme === "dark" || value.theme === "system" ? value.theme : "light",
      density: value.density === "comfortable" ? "comfortable" : "compact",
      answerDetail: ["concise", "standard", "detailed"].includes(value.answerDetail ?? "") ? value.answerDetail as AnswerDetail : "standard",
      evidenceDisplay: value.evidenceDisplay === "compact" ? "compact" : "expanded",
    };
  } catch {
    return DEFAULT_UI_PREFERENCES;
  }
}

export function resolveUiTheme(theme: UiTheme, prefersDark = false): ResolvedUiTheme {
  return theme === "system" ? (prefersDark ? "dark" : "light") : theme;
}

export function writeUiPreferences(preferences: UiPreferences, storage: Pick<Storage, "setItem"> | null = typeof window === "undefined" ? null : window.localStorage): void {
  storage?.setItem(UI_PREFERENCES_KEY, JSON.stringify(preferences));
}
