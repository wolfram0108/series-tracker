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

  it("says nothing when there is no reason", () => {
    expect(reasonText(null)).toBe("")
    expect(reasonText("")).toBe("")
  })
})
