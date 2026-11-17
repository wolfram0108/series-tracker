import { describe, expect, it } from "vitest"
import {
  canJoin, canJoinRange, cellsIn, emptyRow, fieldValues, join, joinRange, joined, rangeOf, rowOf, rowsOf, settle, spanOf, split,
  splitRange, storedOf, textsOf,
} from "./ruleTable"

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
    hand.if.blocks = [{ id: 1, type: "text", value: "V:HORIZON" }, { id: 2, type: "or" }, { id: 3, type: "text", value: "mir.donghua" }]
    expect(textsOf([rowOf("V", "AniStar"), hand])).toEqual(new Set(["V:AniStar", "V:HORIZON", "mir.donghua"]))
    expect(rowOf("V", "AniStar").then.value).toBe("AniStar")
  })

  it("is stored as the core takes it and read back the same, whatever the action of its column", () => {
    const episode = emptyRow()
    episode.if.blocks = [{ id: 1, type: "text", value: "Серия" }, { id: 2, type: "number" }]
    episode.then.blocks = [{ id: 3, type: "number" }]
    const voices = storedOf("assign_voiceover", [rowOf("V", "AniStar")])
    expect(voices).toEqual({ action_type: "assign_voiceover", rows: [{ if: [{ type: "text", value: "V:AniStar" }], then: "AniStar" }] })
    const episodes = storedOf("extract_single", [episode])
    expect(episodes.rows).toEqual([{ if: [{ type: "text", value: "Серия" }, { type: "number" }], then: [{ type: "number" }] }])
    expect(storedOf("exclude", [episode]).rows[0].then).toBeNull()
    for (const table of [voices, episodes]) expect(storedOf(table.action_type, rowsOf(table))).toEqual(table)
  })

  it("keeps the action of a row that is not the column's, with its value written for it, and drops the column's own", () => {
    const episode = emptyRow()
    episode.if.blocks = [{ id: 1, type: "text", value: "Dir" }]
    episode.then.blocks = [{ id: 2, type: "number" }]
    const tag = rowOf("T", "DIR")
    tag.then.action = "assign_video_tag"
    const same = rowOf("S", "1")
    same.then.action = "extract_single"
    const stored = storedOf("extract_single", [episode, tag])
    expect(stored.rows).toEqual([
      { if: [{ type: "text", value: "Dir" }], then: [{ type: "number" }] },
      { if: [{ type: "text", value: "T:DIR" }], then: "DIR", action_type: "assign_video_tag" }])
    expect(rowsOf(stored).map((row) => row.then.action)).toEqual([undefined, "assign_video_tag"])
    expect(storedOf("extract_single", rowsOf(stored))).toEqual(stored)
    expect(storedOf("extract_single", [same]).rows[0].action_type).toBeUndefined()
  })

  it("reads a number a table was stored with as the text of its value", () => {
    const stored = { action_type: "assign_season", rows: [{ if: [{ type: "text", value: "PID:site" }], then: 4 as unknown as string }] }
    expect(rowsOf(stored)[0].then.value).toBe("4")
    expect(storedOf("assign_season", rowsOf(stored)).rows[0].then).toBe("4")
  })

  // --- joined cells -------------------------------------------------------------------------------------------------

  const voices = () => [rowOf("V", "ChinaTown"), rowOf("V", "ChineTown"), rowOf("V", "AniStar")]

  it("joins a cell with the one below: the rows hold one cell, and what is typed into it is typed for each", () => {
    const rows = voices()
    join(rows, 0, "then")
    expect([joined(rows, 1, "then"), joined(rows, 2, "then"), joined(rows, 1, "if")]).toEqual([true, false, false])
    expect(spanOf(rows, 1, "then")).toEqual([0, 2])
    rows[0].then.value = "ChinaTown (оба написания)"
    // For the core each row is whole: both rows carry the value of the joined cell, the second marked as joined.
    expect(storedOf("assign_voiceover", rows).rows.map((row) => [row.if[0].value, row.then, row.then_joined ?? false])).toEqual([
      ["V:ChinaTown", "ChinaTown (оба написания)", false], ["V:ChineTown", "ChinaTown (оба написания)", true],
      ["V:AniStar", "AniStar", false]])
    // Joined again from any of its rows, the cell takes the next one below.
    join(rows, 1, "then")
    expect(spanOf(rows, 0, "then")).toEqual([0, 3])
  })

  it("does not join rows that are one cell on the other side already, nor the last cell with nothing", () => {
    const rows = voices()
    join(rows, 0, "if")
    expect(canJoin(rows, 0, "then")).toBe(false)  // the two rows would be one rule written twice
    expect(canJoin(rows, 2, "if")).toBe(false)
    expect(canJoin(rows, 1, "if")).toBe(true)
    join(rows, 0, "then")
    expect(joined(rows, 1, "then")).toBe(false)
  })

  it("splits a joined cell into the cells of its rows, each with what it held", () => {
    const rows = voices()
    join(rows, 0, "if")
    join(rows, 0, "if")
    split(rows, 2, "if")
    expect([1, 2].map((at) => joined(rows, at, "if"))).toEqual([false, false])
    expect(rows.map((row) => row.if.blocks[0].value)).toEqual(["V:ChinaTown", "V:ChinaTown", "V:ChinaTown"])
    rows[1].if.blocks[0].value = "V:другая"
    expect(rows[0].if.blocks[0].value).toBe("V:ChinaTown")
  })

  it("keeps a cell a run of neighbouring rows when rows are moved or removed", () => {
    const rows = voices()
    join(rows, 0, "then")
    // The second row of the joined cell dragged to the end: it has a cell of its own with the same value.
    rows.push(rows.splice(1, 1)[0])
    settle(rows)
    expect(rows.map((row) => row.then.value)).toEqual(["ChinaTown", "AniStar", "ChinaTown"])
    expect(rows.map((_row, at) => joined(rows, at, "then"))).toEqual([false, false, false])
    rows[2].then.value = "отдельно"
    expect(rows[0].then.value).toBe("ChinaTown")
  })

  it("makes several chosen cells one: it holds what the uppermost held, what the others held is dropped", () => {
    const rows = [...voices(), rowOf("V", "FumoDub")]
    // The choice from the second row to the fourth, whichever of the two was clicked first: three cells.
    expect(rangeOf(rows, 3, 1, "then")).toEqual([1, 4])
    expect(cellsIn(rows, 1, 4, "then")).toBe(3)
    joinRange(rows, 1, 4, "then")
    expect(rows.map((row) => row.then.value)).toEqual(["ChinaTown", "ChineTown", "ChineTown", "ChineTown"])
    expect(spanOf(rows, 2, "then")).toEqual([1, 4])
    // A choice that ends inside a joined cell takes the whole of it; one cell alone is nothing to join.
    expect(rangeOf(rows, 0, 2, "then")).toEqual([0, 4])
    expect(canJoinRange(rows, 1, 4, "then")).toBe(false)
    expect(canJoinRange(rows, 0, 4, "then")).toBe(true)
    // Cells that are one cell on the other side already are not joined.
    expect(canJoinRange(rows, 1, 3, "if")).toBe(false)
    expect(canJoinRange(rows, 0, 2, "if")).toBe(true)
  })

  it("splits every joined cell of the chosen rows, each row keeping what the joined cell held", () => {
    const rows = [...voices(), rowOf("V", "FumoDub")]
    joinRange(rows, 0, 2, "then")
    joinRange(rows, 2, 4, "then")
    splitRange(rows, 0, 4, "then")
    expect(rows.map((_row, at) => joined(rows, at, "then"))).toEqual([false, false, false, false])
    expect(rows.map((row) => row.then.value)).toEqual(["ChinaTown", "ChinaTown", "AniStar", "AniStar"])
    rows[1].then.value = "своя"
    expect(rows[0].then.value).toBe("ChinaTown")
  })

  it("is read back with its joined cells as it was stored", () => {
    const rows = voices()
    join(rows, 0, "then")
    const stored = storedOf("assign_voiceover", rows)
    const read = rowsOf(stored)
    expect(read.map((_row, at) => joined(read, at, "then"))).toEqual([false, true, false])
    expect(storedOf("assign_voiceover", read)).toEqual(stored)
  })
})
