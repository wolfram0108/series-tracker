import { test, expect } from "@playwright/test"
import { openApp } from "./helpers"

const API = "http://127.0.0.1:5000"

test.describe("Логи", () => {
  test("фильтры по умолчанию — Все группы / Все уровни", async ({ page }) => {
    await openApp(page)
    await page.getByRole("button", { name: "Просмотр логов" }).click()
    await expect(page.locator(".logs-filters")).toContainText("Все группы")
    await expect(page.locator(".logs-filters")).toContainText("Все уровни")
  })

  test("Настройка: группы модулей + флаги дампа; тумблер группы round-trip", async ({ page, request }) => {
    await openApp(page)
    await page.getByRole("button", { name: "Просмотр логов" }).click()
    await page.locator(".st-tab[title='Настройка']").click()
    await expect(page.locator(".logging-groups .modern-fieldset").first()).toBeVisible()
    expect(await page.locator(".dump-grid .debug-switch").count()).toBeGreaterThan(0)

    const scanFlag = async (): Promise<boolean> => {
      const d = await (await request.get(`${API}/api/settings/debug_flags`)).json()
      for (const mods of Object.values(d.logging_modules) as { name: string; enabled: boolean }[][]) {
        for (const m of mods) if (m.name === "scan") return m.enabled
      }
      return false
    }
    const before = await scanFlag()
    const grp = page.locator(".logging-group-header", { hasText: "Конвейер" }).locator(".p-toggleswitch")
    await grp.click()
    await page.waitForTimeout(900)
    expect(await scanFlag()).toBe(!before)
    await grp.click() // вернуть
    await page.waitForTimeout(900)
    expect(await scanFlag()).toBe(before)
  })
})
