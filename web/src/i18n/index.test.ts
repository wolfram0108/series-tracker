import { describe, expect, it } from "vitest"
import { t } from "./index"

describe("t", () => {
  it("fills placeholders from the params", () => {
    expect(t("add.source.coverage", { ranges: "1-10, 13", count: 11 })).toBe("1-10, 13 (11 сер.)")
  })

  it("leaves a catalog text without placeholders as is", () => {
    expect(t("add.source.label")).toBe("Плеер и озвучка")
  })

  it("renders a missing param as an empty string", () => {
    expect(t("add.source.coverage", { ranges: "1-2" })).toBe("1-2 ( сер.)")
  })
})
