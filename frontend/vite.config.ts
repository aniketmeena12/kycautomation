import path from "node:path"
import { defineConfig } from "vite"
import react from "@vitejs/plugin-react"

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": path.resolve(__dirname, "./src") } },
  server: {
    port: 5173,
    // The backend is same-origin in dev, so the app never needs CORS or a
    // hardcoded host. VITE_API_URL overrides for a deployed split origin.
    proxy: { "/api": { target: "http://localhost:8000", changeOrigin: true },
             "/health": { target: "http://localhost:8000", changeOrigin: true } },
  },
  test: {
    // The forks pool times out on this repo's path; threads is reliable here.
    pool: "threads",
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    css: false,
  },
})
