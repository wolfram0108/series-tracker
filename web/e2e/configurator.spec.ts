import { test, expect, request as apiRequest } from "@playwright/test"
import { openApp, openSettings } from "./helpers"

const API = "http://127.0.0.1:5000"

// Самоочистка: убрать любые e2e-tmp-* профили, даже если тест упал на середине
// (стенд должен оставаться в исходном состоянии).
test.afterAll(async () => {
  const ctx = await apiRequest.newContext({ baseURL: API })
  const profiles = (await (await ctx.get("/api/parser-profiles")).json()) as { id: number; name: string }[]
  for (const p of profiles) {
    if (p.name?.startsWith("e2e-tmp-")) await ctx.delete(`/api/parser-profiles/${p.id}`)
  }
  await ctx.dispose()
})

async function pickProfile(page: import("@playwright/test").Page, name: string) {
  await page.locator(".parser-accordion .item-select").first().click()
  await page.locator("body > .options-list .option", { hasText: name }).first().click()
}

test.describe("Конфигуратор «Фильтры VK»", () => {
  test("выбор профиля → правила → раскрытие правила с блоками", async ({ page }) => {
    await openApp(page)
    await openSettings(page, "Фильтры VK")
    await pickProfile(page, "Гробница богов")
    await expect(page.locator(".rule-card").first()).toBeVisible()
    await page.locator(".parser-accordion .rule-toggle-icon").first().click()
    await expect(page.locator(".if-block").first()).toBeVisible()
    await expect(page.locator(".pattern-palette").first()).toBeVisible()
  })

  test("CRUD профиля round-trip: создать → удалить (нетто без изменений)", async ({ page, request }) => {
    await openApp(page)
    await openSettings(page, "Фильтры VK")
    const name = "e2e-tmp-" + Date.now()
    await page.locator("#new-profile-name").fill(name)
    await page.locator(".btn-add[title='Создать']").click()
    await page.waitForTimeout(900)
    // создан в БД
    const list = await (await request.get(`${API}/api/parser-profiles`)).json()
    expect(list.some((p: { name: string }) => p.name === name)).toBeTruthy()
    // удалить: переоткрыть Шаг 1 (после создания активен Шаг 2)
    await page.locator(".accordion-button", { hasText: "Шаг 1" }).click()
    await page.locator(".parser-accordion .btn-delete[title='Удалить']").click()
    await page.getByRole("button", { name: "Подтвердить" }).click()
    await page.waitForTimeout(900)
    const after = await (await request.get(`${API}/api/parser-profiles`)).json()
    expect(after.some((p: { name: string }) => p.name === name)).toBeFalsy()
  })

  test("тест профиля: прогон правил → карточка исключения", async ({ page }) => {
    await openApp(page)
    await openSettings(page, "Фильтры VK")
    await pickProfile(page, "AniDub VK Rules")
    await page.locator(".accordion-button", { hasText: "Шаг 3" }).click()
    await page.locator(".parser-test-textarea").fill("Трейлер нового сезона\nОбычное видео")
    await page.getByRole("button", { name: "Запустить тест" }).click()
    await expect(page.locator(".card-test-result").first()).toBeVisible()
    await expect(page.locator(".card-test-result").first()).toContainText("ИСКЛЮЧЕНО")
  })
})
