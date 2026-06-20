import { test, expect } from "@playwright/test"
import { openApp } from "./helpers"

test.describe("Главный экран и инварианты", () => {
  test("список и шапка отрисованы", async ({ page }) => {
    await openApp(page)
    expect(await page.locator(".series-card").count()).toBeGreaterThan(0)
    await expect(page.getByRole("button", { name: "Добавить сериал" })).toBeVisible()
    await expect(page.getByRole("button", { name: "Просмотр логов" })).toBeVisible()
    await expect(page.getByRole("button", { name: "Настройки" })).toBeVisible()
    // у карточки есть действия статус/скан/удалить
    const first = page.locator(".series-card").first()
    await expect(first.locator("[title='Статус']")).toBeVisible()
    await expect(first.locator("[title='Сканировать']")).toBeVisible()
    await expect(first.locator("[title='Удалить']")).toBeVisible()
  })

  test("инвариант: одно SSE-соединение и один GET /api/series при загрузке", async ({ page }) => {
    const streams: string[] = []
    const seriesGets: string[] = []
    page.on("request", (r) => {
      const u = r.url()
      if (u.includes("/api/stream")) streams.push(u)
      if (r.method() === "GET" && /\/api\/series(\?|$)/.test(u)) seriesGets.push(u)
    })
    await openApp(page)
    await page.waitForTimeout(2500)
    expect(streams.length, "ровно одно /api/stream").toBe(1)
    expect(seriesGets.length, "ровно один GET /api/series").toBe(1)
  })
})
