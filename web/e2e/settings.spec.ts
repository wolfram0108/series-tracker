import { test, expect } from "@playwright/test"
import { openApp, openSettings } from "./helpers"

const API = "http://127.0.0.1:5000"

test.describe("Настройки", () => {
  test("все вкладки открываются", async ({ page }) => {
    await openApp(page)
    await page.getByRole("button", { name: "Настройки" }).click()
    for (const t of ["Авторизация", "Трекеры", "Фильтры VK", "Агенты", "Отладка"]) {
      await page.locator(`.st-tab[title='${t}']`).click()
      await expect(page.locator(".modal-title")).toContainText("Настройки")
    }
  })

  test("Отладка: тумблер флага round-trip + просмотр БД", async ({ page, request }) => {
    await openApp(page)
    await openSettings(page, "Отладка")

    const flag = async () =>
      (await (await request.get(`${API}/api/settings/force_replace`)).json()).enabled
    const before = await flag()
    const sw = page
      .locator(".flags-grid .debug-switch", { hasText: "Всегда заменять торренты" })
      .locator(".p-toggleswitch")
    await sw.click()
    await page.waitForTimeout(700)
    expect(await flag()).toBe(!before)
    await sw.click() // вернуть
    await page.waitForTimeout(700)
    expect(await flag()).toBe(before)

    // просмотр БД: таблицы-вкладки загрузились
    await page.getByRole("button", { name: "Просмотр БД" }).click()
    await expect(page.locator(".db-tab").first()).toBeVisible()
    expect(await page.locator(".db-tab").count()).toBeGreaterThan(5)
    await page.locator(".modern-close").last().click()
  })
})
