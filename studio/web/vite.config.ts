import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Built files are served by `python -m studio serve` under /studio.
// `npm run dev` proxies the API to that server for hot-reload work.
const backend = "http://127.0.0.1:4760";

export default defineConfig({
  base: "/studio/",
  plugins: [react()],
  server: {
    proxy: {
      "/api": { target: backend, changeOrigin: true },
      "/media": { target: backend, changeOrigin: true },
      "/thumb": { target: backend, changeOrigin: true },
    },
  },
});
