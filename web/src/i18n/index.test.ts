import { describe, expect, it } from "vitest"
import { t, tn, type MessageKey } from "./index"

describe("t", () => {
  it("fills placeholders from the params", () => {
    expect(t("sources.progress", { phase: "Страницы серий", current: 3, total: 20 })).toBe("Страницы серий: 3 из 20")
  })

  it("leaves a catalog text without placeholders as is", () => {
    expect(t("sources.add.button")).toBe("Добавить источник")
  })

  it("shows a key the catalog lacks as the key, never failing the screen", () => {
    expect(t("voices.settings.priority.kind.newer" as MessageKey)).toBe("voices.settings.priority.kind.newer")
  })

  it("renders a missing param as an empty string", () => {
    expect(t("sources.progress", { phase: "Каталог плеера" })).toBe("Каталог плеера:  из ")
  })

  it("has a text for each cause behind the state of a voice-over", () => {
    for (const hint of ["planned", "download_queued", "downloading", "ready", "awaiting_answer", "aligning_wait", "aligning_run", "aligned", "done", "packed"]) {
      expect(t(`voices.hint.${hint}` as MessageKey)).not.toBe("")
    }
  })
})

describe("tn", () => {
  it("picks the plural form of the language for the count", () => {
    const rows = [1, 2, 4, 5, 11, 12, 21, 22, 25, 101].map((count) => tn("sources.count.rows", count))
    expect(rows).toEqual([
      "1 строка", "2 строки", "4 строки", "5 строк", "11 строк", "12 строк", "21 строка", "22 строки", "25 строк", "101 строка",
    ])
  })

  it("takes the many-form for zero and for a fraction", () => {
    expect(tn("sources.count.rows", 0)).toBe("0 строк")
    expect(tn("sources.count.rows", 1.5)).toBe("1.5 строк")
  })
})
