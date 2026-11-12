import { describe, expect, it } from "vitest"
import { outlinePieces, type Geometry } from "./outline"

const GEOMETRY: Geometry = { x0: 0, y0: 0, pitchX: 110, pitchY: 60, w: 100, h: 50, radius: 8, rows: 3, cols: 3 }

describe("outline of groups of cards", () => {
  // A 3×3 grid: the group 0 holds the cells (0,0) and (1,1) — a diagonal — and 1 holds (0,1).
  const groups: Record<string, number> = { "0,0": 0, "1,1": 0, "0,1": 1 }
  const at = (row: number, col: number) => groups[`${row},${col}`] ?? -1
  const pieces = outlinePieces(GEOMETRY, at)

  it("joins two cells of one group that touch only at a corner", () => {
    // The crossing between (0,0), (0,1), (1,0), (1,1): neither cell of 0 is rounded there, so they meet at the crossing.
    const tile00 = pieces.find((piece) => piece.row === 0 && piece.col === 0 && piece.owner === 0 && piece.d.includes("Z"))!
    expect(tile00.d).toContain("L 105 55")
    // The corners of the others at that crossing are rounded and their bits go to the group of the diagonal.
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
