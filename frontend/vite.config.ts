import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ command }) => ({
  plugins: [react()],
  // /desktop is the sole production static-host prefix. Vite development
  // remains rooted at / so localhost:5173 keeps its existing contract.
  base: command === "build" ? "/desktop/" : "/",
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8765",
        changeOrigin: true,
      },
    },
  },
}));
