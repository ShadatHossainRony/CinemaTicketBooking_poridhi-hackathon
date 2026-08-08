import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "dist",
    sourcemap: false,
  },
  server: {
    port: 5173,
    proxy: {
      // Dev proxy so the frontend can call the API at the same origin.
      // The API is mounted at the ROOT (no /api, no /v1) — Nginx uses
      // per-prefix allow-list (see nginx/cinemaseat.conf).
      "^/(health|ready|movies|theatres|shows|holds|bookings|docs|openapi.json)": {
        target: "http://localhost:8000",
        changeOrigin: false,
      },
    },
  },
});
