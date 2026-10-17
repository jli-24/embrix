export type ResearchMode = "AUTO" | "LOCAL_ONLY" | "WEB_REQUIRED";

export const RESEARCH_MODE_LABELS: Record<ResearchMode, string> = {
  AUTO: "Auto",
  LOCAL_ONLY: "Local Only",
  WEB_REQUIRED: "Web Required",
};

export const RESEARCH_MODE_OPTIONS: readonly ResearchMode[] = ["AUTO", "LOCAL_ONLY", "WEB_REQUIRED"];

export const DEFAULT_RESEARCH_MODE: ResearchMode = "AUTO";
