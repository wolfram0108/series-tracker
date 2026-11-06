import { describe, expect, it } from "vitest"
import { badgeOf, cardStatus, letterOf, membersOf, outlinePieces, shareOf, takes, type CompilationView, type Geometry } from "./compilations"

const GEOMETRY: Geometry = { x0: 0, y0: 0, pitchX: 110, pitchY: 60, w: 100, h: 50, radius: 8, rows: 3, cols: 3 }

function view(index: number, start: number, end: number, members: number[]): CompilationView {
  return {
    season: 1, index, take: true,
    ref: { key: `k${index}`, source_id: 1, row_key: "r", player: "vk", voice_group: "ПВА", height: 2160, start, end },
    episodes: Array.from({ length: end - start + 1 }, (_, at) => ({
      episode: start + at, member: members.includes(start + at), absence: members.includes(start + at) ? null : "elsewhere",
      elsewhere: null, own: false,
    })),
  }
}

describe("compilations", () => {
  it("names compilations by letters that go on past Z", () => {
    expect([0, 1, 25, 26, 27].map(letterOf)).toEqual(["A", "B", "Z", "AA", "AB"])
  })

  it("gives colours round the sixteen tokens and finds the compilation of each episode", () => {
    expect(badgeOf(view(17, 1, 2, [])).color).toBe(2)
    const members = membersOf([view(0, 1, 3, [1, 2]), view(1, 3, 4, [3])])
    expect([members.get("1:1")?.letter, members.get("1:3")?.letter, members.get("1:4")]).toEqual(["A", "B", undefined])
  })

  it("does not take an episode whose reference now is as high as the compilation, though the layout puts it in", () => {
    const compilation = view(0, 1, 3, [1, 2, 3])
    const height = (_season: number, episode: number) => (episode === 3 ? 2160 : episode === 2 ? 720 : null)
    expect([1, 2, 3].map((episode) => takes(compilation, episode, height))).toEqual([true, true, false])
    expect([...membersOf([compilation], height).keys()]).toEqual(["1:1", "1:2"])
  })

  it("colours a card by its cut and turns off an episode that is not in the compilation", () => {
    const cut = { series_id: 1, season: 1, episode: 1, part: 0, cut_key: "k", status: "aligning" }
    expect(cardStatus(false, cut)).toBe("off")
    expect(cardStatus(true, cut)).toBe("align")
    expect(cardStatus(true, undefined)).toBe("wait")
    expect(cardStatus(true, undefined, { key: "k" })).toBe("ready")
    expect(shareOf({ ...cut, from_ref_s: 1260, from_dub_s: 40 })).toBeCloseTo(0.969, 3)
  })

  // A 3×3 grid: the compilation 0 holds the cells (0,0) and (1,1) — a diagonal — and 1 holds (0,1).
  const groups: Record<string, number> = { "0,0": 0, "1,1": 0, "0,1": 1 }
  const at = (row: number, col: number) => groups[`${row},${col}`] ?? -1
  const pieces = outlinePieces(GEOMETRY, at)

  it("joins two cells of one compilation that touch only at a corner", () => {
    // The crossing between (0,0), (0,1), (1,0), (1,1): neither cell of 0 is rounded there, so they meet at the crossing.
    const tile00 = pieces.find((piece) => piece.row === 0 && piece.col === 0 && piece.owner === 0 && piece.d.includes("Z"))!
    expect(tile00.d).toContain("L 105 55")
    // The corners of the others at that crossing are rounded and their bits go to the compilation of the diagonal.
    expect(pieces.filter((piece) => piece.owner === 0 && piece.d.startsWith("M") && piece.d.includes("A")).length).toBeGreaterThan(0)
  })

  it("rounds the corner of a lone cell concentric with its card, its outer edge on the centre of the gap", () => {
    const lone = pieces.find((piece) => piece.row === 0 && piece.col === 1 && piece.owner === 1 && !piece.d.includes(" L 215 -5"))!
    // radius R1 + half the gap: 8 + 5
    expect(lone.d).toContain("A 13 13 0 0 1")
  })

  it("gives every cell of the grid and the ring around it a piece", () => {
    const cells = pieces.filter((piece) => !piece.d.includes("A") || piece.d.split("L").length > 3)
    expect(new Set(cells.map((piece) => `${piece.row},${piece.col}`)).size).toBe(25)
  })
})
