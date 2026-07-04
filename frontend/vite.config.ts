import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Production build output lands in resource/public, which app/asgi.py already
// mounts at "/" via StaticFiles - so `python main.py` serves the dashboard and
// the API from a single port with no extra wiring.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../resource/public",
    emptyOutDir: true,
  },
  server: {
    proxy: {
      "/api": { target: "http://127.0.0.1:8080", ws: true },
      "/tasks": "http://127.0.0.1:8080",
      "/stream": "http://127.0.0.1:8080",
      "/download": "http://127.0.0.1:8080",
    },
  },
});
