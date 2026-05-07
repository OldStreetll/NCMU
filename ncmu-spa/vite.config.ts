import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

// PLAN-FIX-3 F3: dev runtime = compose service `ncmu-spa` (node:20-alpine on
// ncmu-net); proxy target uses container DNS `ncmu-backend`. Host-direct
// `npm run dev` is a fallback — needs ncmu-backend `ports: ["8000:8000"]`
// publish OR target swap to `http://localhost:80` (route via ncmu-nginx).
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "src") },
  },
  server: {
    host: "0.0.0.0",
    port: 5173,
    strictPort: true,
    proxy: {
      // PLAN-FIX-2 Q3: forward /api/v1/ncmu/* to ncmu-backend; no rewrite
      // (backend mounts the same prefix). PLAN-FIX-4 C9-1: SSE chunked
      // streams must pass through unbuffered — Vite's default proxy uses
      // http-proxy-middleware which streams responses by default.
      "/api/v1/ncmu": {
        target: "http://ncmu-backend:8000",
        changeOrigin: true,
      },
    },
    watch: {
      usePolling: true,
      interval: 1000,
    },
  },
});
