import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Dev server proxies /api to the FastAPI container published on :8000.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      // ws: true lets the Kite depth stream (WS /api/kite/depth/{isin})
      // pass through the dev proxy too.
      "/api": { target: "http://localhost:8000", ws: true },
    },
  },
});
