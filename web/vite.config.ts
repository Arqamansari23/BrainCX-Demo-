import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The API and the live-update socket are proxied, so the browser sees one origin
// and the session cookie reaches /api (including the LiveKit token endpoint) and /ws.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5174,
    // Fail instead of silently moving to another port, where the API's CORS and
    // cookie settings wouldn't match.
    strictPort: true,
    proxy: {
      "/api": { target: "http://localhost:8001", changeOrigin: true },
      "/ws": { target: "ws://localhost:8001", ws: true },
    },
  },
  // livekit-client alone is ~600 kB; fine for a prototype, so don't warn about it.
  build: { chunkSizeWarningLimit: 1000 },
});
