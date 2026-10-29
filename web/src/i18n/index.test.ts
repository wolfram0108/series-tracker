import { describe, expect, it } from "vitest"
import { t, type MessageKey } from "./index"

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

  it("has a text for each of the seven states of a voice-over and for each cause behind them", () => {
    for (const state of ["planned", "download_queued", "downloading", "align_queued", "running", "done", "error"]) {
      expect(t(`voices.state.${state}` as MessageKey)).not.toBe("")
    }
    for (const hint of ["planned", "download_queued", "downloading", "ready", "awaiting_answer", "aligning_wait", "aligning_run", "aligned", "done"]) {
      expect(t(`voices.hint.${hint}` as MessageKey)).not.toBe("")
    }
    expect(t("voices.state.download_queued")).toBe("в очереди на загрузку")
    expect(t("voices.state.align_queued")).toBe("в очереди на выравнивание")
  })
})
