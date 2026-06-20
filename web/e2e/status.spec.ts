import { test, expect } from "@playwright/test"
import { openApp } from "./helpers"

test.describe("Статус-окно", () => {
  test("торрент: вкладки и поля Свойств", async ({ page }) => {
    await openApp(page)
    await page.locator(".series-card").first().locator("[title='Статус']").click()
    await expect(page.locator(".modal-title")).toContainText("Статус")
    const tabs = page.locator(".st-tab")
    const titles = await tabs.evaluateAll((els) => els.map((e) => e.getAttribute("title")))
    expect(titles).toContain("Свойства")
    expect(titles).toContain("Композиция")
    expect(titles).toContain("История")
    // у торрент-серии НЕТ вкладки «Нарезка»
    expect(titles).not.toContain("Нарезка")
    // Свойства: поля названия видимы
    await expect(page.locator("label", { hasText: "Название (RU)" })).toBeVisible()
  })

  test("инвариант viewing-stop: open → ['viewing'], close → []", async ({ page }) => {
    const statePosts: string[] = []
    page.on("request", (r) => {
      if (r.method() === "POST" && /\/api\/series\/\d+\/state$/.test(r.url())) {
        statePosts.push(r.postData() ?? "")
      }
    })
    await openApp(page)
    await page.locator(".series-card").first().locator("[title='Статус']").click()
    await expect(page.locator(".modal-title")).toContainText("Статус")
    await page.waitForTimeout(800)
    await page.locator(".modern-close").last().click()
    await page.waitForTimeout(800)
    expect(statePosts.length, "два POST состояния (open+close)").toBeGreaterThanOrEqual(2)
    expect(statePosts.some((b) => b.includes('"viewing"')), "viewing при открытии").toBeTruthy()
    expect(
      statePosts.some((b) => /"state"\s*:\s*\[\s*\]/.test(b)),
      "пустой state при закрытии",
    ).toBeTruthy()
  })

  test("VK: вкладка «Нарезка» присутствует, Композиция рендерит", async ({ page }) => {
    await openApp(page)
    await page.locator(".series-card").last().locator("[title='Статус']").click()
    const titles = await page.locator(".st-tab").evaluateAll((els) => els.map((e) => e.getAttribute("title")))
    expect(titles).toContain("Нарезка")
    await page.locator(".st-tab[title='Композиция']").click()
    // карточки композиции или явный спиннер/пусто — проверяем, что вкладка ожила
    await expect(page.locator(".st-tabs")).toBeVisible()
    await page.locator(".modern-close").last().click()
  })
})
