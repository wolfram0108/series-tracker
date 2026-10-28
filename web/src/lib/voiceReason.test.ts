import { describe, expect, it } from "vitest"
import { reasonText } from "./voiceReason"

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
})
