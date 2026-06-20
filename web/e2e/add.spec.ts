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

  test("клик-вне НЕ закрывает, если жест начался внутри окна (данные целы)", async ({ page }) => {
    await openApp(page)
    await page.getByRole("button", { name: "Добавить сериал" }).click()
    const inp = page.getByLabel("URL для парсинга")
    await inp.fill("важные-данные")
    const box = (await inp.boundingBox())!
    // выделение в поле → отпускание за окном (mousedown внутри, mouseup на оверлее)
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
    await page.mouse.down()
    await page.mouse.move(40, 40, { steps: 6 })
    await page.mouse.up()
    // эмулируем Chromium: click пришёлся на общий предок (оверлей)
    await page.evaluate(() =>
      document.querySelector(".modal-overlay")!.dispatchEvent(new MouseEvent("click", { bubbles: true })),
    )
    await expect(page.locator(".modal-title")).toBeVisible()
    await expect(inp).toHaveValue("важные-данные")
    // честный клик по фону (жест начался на оверлее) — закрывает
    await page.mouse.click(20, 20)
    await expect(page.locator(".modal-title")).toHaveCount(0)
  })
})
