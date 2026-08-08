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
      "^/(health|ready|movies|theatres|shows|holds|bookings|payments|docs|openapi.json)": {
        target: "http://localhost:8000",
        changeOrigin: false,
      },
    },
  },
});
