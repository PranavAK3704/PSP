import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The backend runs on :8077 (see backend/run). Proxy /api so the frontend can
// call it same-origin (SSE included).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5190,
    open: true,
    proxy: {
      "/api": { target: "http://localhost:8077", changeOrigin: true },
    },
  },
});
