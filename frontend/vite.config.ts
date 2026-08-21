import path from "node:path"
import { defineConfig } from "vite"
import react from "@vitejs/plugin-react"
import tailwindcss from "@tailwindcss/vite"

// The build lands inside the Python package, which is what FastAPI serves in
// production — there is no second web server in this deployment. `jobd serve`
// mounts ../src/jobd/web/static and falls back to index.html for any path the
// API does not own, so a deep link like /company/<uuid> resolves.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": path.resolve(__dirname, "./src") } },
  build: {
    outDir: path.resolve(__dirname, "../src/jobd/web/static"),
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
})
