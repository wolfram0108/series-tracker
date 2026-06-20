import { defineConfig, devices } from "@playwright/test"

// E2e против ЖИВОГО стенда (:5000, /v2). Приложение поднято под systemd —
// свой webServer не запускаем. Тесты мутируют данные только обратимо
// (создать→удалить, тумблер on→off), поэтому workers:1 и без параллелизма:
// общий бэкенд, гонки недопустимы. SSE вечный → навигация domcontentloaded
// (networkidle не наступает — это норма).
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 45_000,
  expect: { timeout: 10_000 },
  reporter: [["list"]],
  use: {
    baseURL: "http://127.0.0.1:5000",
    headless: true,
    actionTimeout: 12_000,
    navigationTimeout: 20_000,
    trace: "retain-on-failure",
  },
  projects: [{ name: "firefox", use: { ...devices["Desktop Firefox"] } }],
})
