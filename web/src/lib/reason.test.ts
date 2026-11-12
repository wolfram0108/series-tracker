import { describe, expect, it } from "vitest"
import { reasonText } from "./reason"

describe("reasonText", () => {
  it("fills the wording of a code with its facts", () => {
    expect(reasonText({ code: "critical", params: { text: "нет общего звука" } })).toBe("критично: нет общего звука")
  })

  it("shows the words of another system as they came", () => {
    expect(reasonText({ code: "external", params: { text: "HTTP 404" } })).toBe("HTTP 404")
  })

  it("names a code the catalog does not know instead of failing", () => {
    expect(reasonText({ code: "from_the_future", params: {} })).toBe("причина: from_the_future")
  })

  it("reads a reason that travels as JSON text in a report", () => {
    expect(reasonText('{"code":"critical","text":"нет общего звука"}')).toBe("критично: нет общего звука")
  })

  it("shows text that is not a coded reason as it came", () => {
    expect(reasonText("страница недоступна")).toBe("страница недоступна")
    expect(reasonText('{"not":"a reason"}')).toBe('{"not":"a reason"}')
  })

  it("words a reason nested in the facts of another one", () => {
    const cause = '{"code":"critical","text":"тишина"}'
    expect(reasonText({ code: "suspect", params: { text: cause } })).toBe("подозрительно: критично: тишина")
  })

  it("words a refused action and the state of its object, and shows codes it does not know as they came", () => {
    expect(reasonText({ code: "not_allowed", params: { action: "sync", state: "scanning" } }))
      .toBe("Действие «синхронизация» недоступно: сейчас «синхронизация»")
    expect(reasonText({ code: "not_allowed", params: { action: "cancel_download", state: "completed" } }))
      .toBe("Действие «отмена загрузки» недоступно: сейчас «готово»")
    expect(reasonText({ code: "not_allowed", params: { action: "later", state: "unseen" } }))
      .toBe("Действие «later» недоступно: сейчас «unseen»")
  })

  it("words the blocks and the place a refused template names by their codes", () => {
    expect(reasonText({ code: "block_repeated", params: { place: "condition", block: "range" } }))
      .toBe("в одном условии может быть только один блок «Диапазон»")
    expect(reasonText({ code: "block_after_number", params: { block: "modifier", after: "text" } }))
      .toBe("блок «±» должен следовать сразу за блоком «Число», а не за блоком «Текст»")
  })

  it("says nothing when there is no reason", () => {
    expect(reasonText(null)).toBe("")
    expect(reasonText("")).toBe("")
  })
})
