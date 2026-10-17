export const designTokens = {
  color: {
    light: {
      background: "#FFFFFF",
      surface: "#FFFFFF",
      surfaceSubtle: "#F9FAFB",
      border: "#E5E7EB",
      text: "#111827",
      textSecondary: "#6B7280",
      primary: "#2563EB",
      success: "#16A34A",
      warning: "#D97706",
      danger: "#DC2626",
    },
    dark: {
      background: "#09090B",
      surface: "#18181B",
      surfaceSubtle: "#111113",
      border: "#27272A",
      text: "#F9FAFB",
      textSecondary: "#A1A1AA",
      primary: "#60A5FA",
      success: "#22C55E",
      warning: "#F59E0B",
      danger: "#EF4444",
    },
  },
  radius: { small: 4, medium: 6, composer: 12 },
  spacing: { 1: 4, 2: 8, 3: 12, 4: 16, 5: 24, 6: 32 },
  typography: {
    sans: 'Inter, "SF Pro Text", "Segoe UI", sans-serif',
    mono: '"JetBrains Mono", "SFMono-Regular", Consolas, monospace',
    title: 16,
    section: 13,
    body: 14,
    metadata: 11,
    code: 14,
  },
  layout: { topBar: 56, explorer: 280, context: 320, composer: 90 },
} as const;

export type DesignTone = "neutral" | "success" | "warning" | "danger" | "primary";
