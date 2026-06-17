import { defineConfig } from "vite"
import vue from "@vitejs/plugin-vue"

// Новый фронт раздаётся под /v2 (рядом со старым на /). Бэкенд — FastAPI :5000.
// В dev /api проксируется на бэкенд. SSE (/api/stream) при необходимости можно
// слушать напрямую на :5000, минуя proxy (ТЗ §8, frontend-rewrite).
export default defineConfig({
  base: "/v2/",
  plugins: [vue()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
  server: {
    proxy: {
      "/api": { target: "http://127.0.0.1:5000", changeOrigin: true },
    },
  },
})
