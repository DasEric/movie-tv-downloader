import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// When running `npm run dev` on your local machine, Vite proxies /api and /ws
// to the backend at :3000. In production the same FastAPI process serves the
// built assets, so no proxy is needed.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://localhost:3000", changeOrigin: true },
      "/api/ws": {
        target: "ws://localhost:3000",
        ws: true,
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: false,
    chunkSizeWarningLimit: 1024,
  },
});
