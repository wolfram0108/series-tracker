import { defineConfig } from "vite"
import vue from "@vitejs/plugin-vue"

// Новый фронт раздаётся с КОРНЯ / (Ф6 cutover; старый — на /legacy). Ассеты
// абсолютные (/assets/*) → грузятся одинаково с / и со старого алиаса /v2.
// Бэкенд — FastAPI :5000; в dev /api проксируется на бэкенд.
export default defineConfig({
  base: "/",
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
