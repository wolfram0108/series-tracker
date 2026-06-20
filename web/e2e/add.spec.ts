import { test, expect } from "@playwright/test"
import { openApp } from "./helpers"

test.describe("Добавление сериала", () => {
  // Только UI/валидация/отмена — без реального POST (чтобы не плодить
  // загрузки на стенде). Создание полного потока покрыто бэкенд-тестами.
  test("модалка открывается, валидация, отмена", async ({ page }) => {
    await openApp(page)
    await page.getByRole("button", { name: "Добавить сериал" }).click()
    await expect(page.locator(".modal-title")).toContainText("Добавить новый сериал")
    await expect(page.getByLabel("URL для парсинга")).toBeVisible()
    // без URL «Добавить» недоступна
    const footer = page.locator(".modern-footer")
    await expect(footer.getByRole("button", { name: "Добавить", exact: true })).toBeDisabled()
    // отмена закрывает окно
    await footer.getByRole("button", { name: "Отмена" }).click()
    await expect(page.locator(".modal-title")).toHaveCount(0)
  })
})
