import { type Page } from "@playwright/test"

// Открыть корень (Ф6: новый фронт на /) и дождаться списка серий
// (SSE вечный → domcontentloaded).
export async function openApp(page: Page): Promise<void> {
  await page.goto("/", { waitUntil: "domcontentloaded" })
  await page.waitForSelector(".series-card", { timeout: 20_000 })
}

// Открыть окно настроек на нужной вкладке (по title сегмента).
export async function openSettings(page: Page, tabTitle: string): Promise<void> {
  await page.getByRole("button", { name: "Настройки" }).click()
  await page.locator(`.st-tab[title='${tabTitle}']`).click()
}
