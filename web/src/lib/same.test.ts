import { describe, expect, it } from "vitest"
import type { QueueRow } from "./output"
import { groupsOf, heldFirst, joined, left, rowKey, sizeOf, split } from "./same"

const row = (source_id: number, player: string, same: number | null = null): QueueRow => ({
  group: "Китайский", source_id, player, player_label: player, height: 2160, episodes: "481", stale: "", allowed: true,
  allowed_level: "default", language: "", subtitle_type: "", default: false, default_level: "default", forced: false, forced_level: "default", same,
})
const variant = (source_id: number, player: string) => ({ group: "Китайский", source_id, player })
const keys = (...rows: QueueRow[]) => new Set(rows.map(rowKey))

describe("the groups of equal variants of a queue", () => {
  const a = row(1, "JD4K"), b = row(1, "zijianm3u8"), c = row(2, "duanju"), d = row(3, "JD4K")

  it("are the value of the lever that makes them, by the numbers the rows carry", () => {
    expect(groupsOf([row(1, "x", 2), row(2, "y"), row(3, "z", 1), row(4, "w", 2), row(5, "v", 1)])).toEqual([
      [variant(3, "z"), variant(5, "v")], [variant(1, "x"), variant(4, "w")]])
    expect(groupsOf([a, b])).toEqual([])
  })

  it("take the picked rows into one group", () => {
    expect(joined([a, b, c, d], keys(a, c, d))).toEqual([[variant(1, "JD4K"), variant(2, "duanju"), variant(3, "JD4K")]])
  })

  it("join the group of a picked row whole: a row picked beside a group makes it the group's, two groups make one", () => {
    const g1 = [row(1, "a", 1), row(2, "b", 1)], g2 = [row(3, "c", 2), row(4, "d", 2)]
    expect(joined([...g1, d, ...g2], keys(g1[0], d))).toEqual([[variant(3, "c"), variant(4, "d")],
      [variant(1, "a"), variant(2, "b"), variant(3, "JD4K")]])
    expect(joined([...g1, ...g2], keys(g1[1], g2[0]))).toEqual([
      [variant(1, "a"), variant(2, "b"), variant(3, "c"), variant(4, "d")]])
  })

  it("change nothing when fewer than two rows come of the pick", () => {
    expect(joined([a, b], keys(a))).toEqual([])
    expect(joined([row(1, "a", 1), row(2, "b", 1), c], keys(row(1, "a", 1)))).toEqual([[variant(1, "a"), variant(2, "b")]])
  })

  it("lose a row that leaves them, and a group left with one row is gone", () => {
    const rows = [row(1, "a", 1), row(2, "b", 1), row(3, "c", 1), row(4, "d", 2), row(5, "e", 2)]
    expect(left(rows, rows[1])).toEqual([[variant(1, "a"), variant(3, "c")], [variant(4, "d"), variant(5, "e")]])
    expect(left(rows, rows[3])).toEqual([[variant(1, "a"), variant(2, "b"), variant(3, "c")]])
  })

  it("come apart by the number of a group, the others staying", () => {
    const rows = [row(1, "a", 1), row(2, "b", 1), row(4, "d", 2), row(5, "e", 2)]
    expect(split(rows, 1)).toEqual([[variant(4, "d"), variant(5, "e")]])
    expect(sizeOf(rows, 2)).toBe(2)
  })
})

describe("the order a queue is shown in", () => {
  it("puts the variant the file holds first among its group, the rest of the queue staying where the core put it", () => {
    const rows = [row(9, "x"), row(1, "a", 1), row(2, "b", 1), row(3, "c", 1), row(8, "y")]
    expect(heldFirst(rows, { source_id: 3, player: "c" }).map((each) => each.source_id)).toEqual([9, 3, 1, 2, 8])
    expect(heldFirst(rows, { source_id: 9, player: "x" }).map((each) => each.source_id)).toEqual([9, 1, 2, 3, 8])
    // Nothing moves: the very list comes back, not a copy of it.
    expect(heldFirst(rows, null)).toBe(rows)
    expect(heldFirst(rows, { source_id: 77, player: "none" })).toBe(rows)
    expect(heldFirst(rows, { source_id: 1, player: "a" })).toBe(rows)
    expect(heldFirst(rows, { source_id: 9, player: "x" })).toBe(rows) // held, but in no group
  })
})
