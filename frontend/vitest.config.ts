import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  root: ".",
  plugins: [react()],
  test: { environment: "jsdom", globals: true, exclude: ["**/node_modules/**", "**/.pnpm-store/**", "**/dist/**"] },
});
