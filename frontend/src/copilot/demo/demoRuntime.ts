import type { RuntimePanel } from "../api/chatClient";
import type { MemoryView, RuntimeHealth } from "../api/environmentClient";

export type DemoRuntime = {
  label: "UI Preview";
  hilLabel: "HIL STATUS";
  edaLabel: "EDA STATUS";
  panel: RuntimePanel;
  memory: MemoryView[];
  health: RuntimeHealth[];
};

export const demoRuntime: DemoRuntime = {
  label: "UI Preview",
  hilLabel: "HIL STATUS",
  edaLabel: "EDA STATUS",
  panel: {
    hil_status: "OBSERVED",
    eda_status: "OBSERVED",
    memory_statuses: ["REVIEW_REQUIRED", "APPROVED", "OBSERVED"],
    runtime_health: [],
  },
  memory: [
    ...Array.from({ length: 12 }, (_, index) => ({ memory_reference: `ui-preview-review-${index + 1}`, status: "REVIEW_REQUIRED" } as const)),
    ...Array.from({ length: 8 }, (_, index) => ({ memory_reference: `ui-preview-approved-${index + 1}`, status: "APPROVED" } as const)),
    ...Array.from({ length: 15 }, (_, index) => ({ memory_reference: `ui-preview-observed-${index + 1}`, status: "OBSERVED" } as const)),
    ...Array.from({ length: 2 }, (_, index) => ({ memory_reference: `ui-preview-failed-${index + 1}`, status: "FAILED" } as const)),
  ],
  health: [
    { component: "Backend", status: "UNAVAILABLE", observed_at: "2026-08-10T14:33:00+08:00" },
    { component: "Knowledge", status: "UNAVAILABLE", observed_at: "2026-08-10T14:33:00+08:00" },
    { component: "AI Service", status: "UNAVAILABLE", observed_at: "2026-08-10T14:33:00+08:00" },
    { component: "Vector DB", status: "UNAVAILABLE", observed_at: "2026-08-10T14:33:00+08:00" },
  ],
};
