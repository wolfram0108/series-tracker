import { describe, expect, it } from "vitest"
import { emptyRow, fieldValues, rowOf, rowsOf, storedOf, textsOf } from "./ruleTable"

const LINES = [
  "PID:1.ruchime.org | P:Kodik | Вечная воля | Серия 1 | V:AniStar | VT:voice | R:1080",
  "PID:1.ruchime.org | P:Kodik | Вечная воля | Серия 1 | V:HORIZON | VT:voice | R:720",
  "PID:dunhuatv.ru | P:Kodik | Вечная воля | Серия 2 | V:AniStar | VT:voice | R:1080",
]

describe("a rule table", () => {
  it("takes every distinct value of a coded field of the lines, in the order it first appears, and no other field's", () => {
    expect(fieldValues(LINES, "V")).toEqual(["AniStar", "HORIZON"])
    expect(fieldValues(LINES, "PID")).toEqual(["1.ruchime.org", "dunhuatv.ru"])
    expect(fieldValues(LINES, "P")).toEqual(["Kodik"])  // "PID:" is not "P:"
    expect(fieldValues(LINES, "G")).toEqual([])
  })

  it("counts a value taken when any row holds its text, a row written by hand too", () => {
    const hand = emptyRow()
    hand.ifBlocks = [{ id: 1, type: "text", value: "V:HORIZON" }, { id: 2, type: "or" }, { id: 3, type: "text", value: "mir.donghua" }]
    expect(textsOf([rowOf("V", "AniStar"), hand])).toEqual(new Set(["V:AniStar", "V:HORIZON", "mir.donghua"]))
    expect(rowOf("V", "AniStar").thenValue).toBe("AniStar")
  })

  it("is stored as the core takes it and read back the same, whatever the action of its column", () => {
    const episode = emptyRow()
    episode.ifBlocks = [{ id: 1, type: "text", value: "Серия" }, { id: 2, type: "number" }]
    episode.thenBlocks = [{ id: 3, type: "number" }]
    const voices = storedOf("assign_voiceover", [rowOf("V", "AniStar")], false)
    expect(voices).toEqual({ action_type: "assign_voiceover", rows: [{ if: [{ type: "text", value: "V:AniStar" }], then: "AniStar" }] })
    const episodes = storedOf("extract_single", [episode], true)
    expect(episodes.rows).toEqual([{ if: [{ type: "text", value: "Серия" }, { type: "number" }], then: [{ type: "number" }] }])
    expect(storedOf("exclude", [episode], false).rows[0].then).toBeNull()
    for (const [table, extract] of [[voices, false], [episodes, true]] as const) {
      expect(storedOf(table.action_type, rowsOf(table), extract)).toEqual(table)
    }
  })

  it("reads a number a table was stored with as the text of its value", () => {
    const stored = { action_type: "assign_season", rows: [{ if: [{ type: "text", value: "PID:site" }], then: 4 as unknown as string }] }
    expect(rowsOf(stored)[0].thenValue).toBe("4")
    expect(storedOf("assign_season", rowsOf(stored), false).rows[0].then).toBe("4")
  })
})
