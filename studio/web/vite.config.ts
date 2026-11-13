import react from "@vitejs/plugin-react";
import path from "node:path";
import { defineConfig } from "vite";

// Built files are served by `python -m studio serve` under /studio.
// `npm run dev` proxies the API to that server for hot-reload work.
const backend = "http://127.0.0.1:4760";

// The timeline preview mounts the repo's own Remotion compositions, so what
// you scrub is the code that renders. Their imports must resolve from this
// package's node_modules (remotion-composer may not be installed), hence dedupe.
const composer = path.resolve(__dirname, "../../remotion-composer/src");

export default defineConfig({
  base: "/studio/",
  plugins: [react()],
  resolve: {
    alias: { "@composer": composer },
    dedupe: [
      "react", "react-dom", "remotion", "@remotion/player", "@remotion/captions", "@remotion/google-fonts",
      "@remotion/media", "@remotion/media-utils", "@remotion/transitions", "d3-geo", "topojson-client", "world-atlas",
    ],
  },
  build: { chunkSizeWarningLimit: 1500 },
  server: {
    fs: { allow: [path.resolve(__dirname, "../..")] },
    proxy: {
      "/api": { target: backend, changeOrigin: true },
      "/media": { target: backend, changeOrigin: true },
      "/thumb": { target: backend, changeOrigin: true },
    },
  },
});
