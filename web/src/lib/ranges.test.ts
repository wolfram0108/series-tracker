import { describe, expect, it } from "vitest"
import { gapsBetween, rangeText, reachOf, runsOfNumbers, runsOfText, spansOf } from "./ranges"

describe("rangeText", () => {
  it("writes a run with its two ends, a run of one with its number", () => {
    expect(rangeText(3, 7)).toBe("3–7")
    expect(rangeText(4, 4)).toBe("4")
  })
})

describe("spansOf", () => {
  it("words a set of numbers as runs the way the core does", () => {
    expect(spansOf([3, 1, 2, 5, 7, 6, 2])).toBe("1–3, 5–7")
    expect(spansOf([4])).toBe("4")
    expect(spansOf([])).toBe("")
    expect(runsOfNumbers([1, 2, 4])).toEqual([[1, 2], [4, 4]])
  })
})

describe("runsOfText", () => {
  it("reads the wording of the core back into its runs", () => {
    expect(runsOfText("1–154, 158–162, 165")).toEqual([[1, 154], [158, 162], [165, 165]])
    expect(runsOfText("")).toEqual([])
  })

  it("puts runs in order and joins the ones that touch or overlap", () => {
    expect(runsOfText("5–9, 1–3, 4, 8–12")).toEqual([[1, 12]])
  })

  it("is null for a piece that is no run: it is not guessed at", () => {
    expect(runsOfText("1–5, S2: 3")).toBeNull()
    expect(runsOfText("1-5")).toBeNull()
  })
})

describe("gapsBetween", () => {
  it("is the numbers missing between the runs", () => {
    expect(gapsBetween([[1, 154], [158, 162], [165, 168], [170, 174], [176, 656]])).toEqual([[155, 157], [163, 164], [169, 169], [175, 175]])
    expect(gapsBetween([[1, 656]])).toEqual([])
  })
})

describe("reachOf", () => {
  it("is the first to the last, the gaps between them written as the sources write them", () => {
    expect(reachOf("1–154, 158–162, 165–168, 170–174, 176–656")).toEqual({ span: "1–656", gaps: "155–157, 163–164, 169, 175" })
  })

  it("has no gaps for a voice-over that holds a whole run or one episode", () => {
    expect(reachOf("0–656")).toEqual({ span: "0–656", gaps: "" })
    expect(reachOf("509")).toEqual({ span: "509", gaps: "" })
  })

  it("says nothing for no episodes", () => {
    expect(reachOf("")).toEqual({ span: "", gaps: "" })
  })

  it("leaves a wording it does not know whole, with no gaps said", () => {
    expect(reachOf("1–5, S2: 3")).toEqual({ span: "1–5, S2: 3", gaps: "" })
  })

  it("is one episode off the first and the last: 526 is the gap of 1–525, 527–656", () => {
    expect(reachOf("1–525, 527–656")).toEqual({ span: "1–656", gaps: "526" })
  })
})
