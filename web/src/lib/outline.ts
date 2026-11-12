/** The outline of groups of cards over a grid (components/status/output/GroupOutline.vue): the compilations of a season,
 *  the episodes selected. Pure geometry of the plane the cards lie in; the groups are only numbers here. */

/** The grid the cards lie in, measured on the page: the corner of the first card, the step between cards, the size of a
 *  card, half of the gaps, the radius of a card, how many rows and columns. */
export interface Geometry {
  x0: number
  y0: number
  pitchX: number
  pitchY: number
  w: number
  h: number
  radius: number
  rows: number
  cols: number
}

/** A piece of the plane: the cell of a card (rounded where the rule says) or the bit of a corner that a rounding gave away,
 *  and the compilation (an index, -1 for none) that owns it. */
export interface Piece {
  row: number
  col: number
  owner: number
  d: string
}

/**
 * The plane cut into cells at the centres of the gaps, each owned by the compilation of its card (`groupAt`, -1 for none or
 * for a place with no card). At every crossing of four cells a corner of a cell is rounded — concentric with the corner of
 * its card, radius R1 + half the gap — when both its neighbours across the gaps are of other compilations and it is not
 * joined to its diagonal neighbour of its own; the bit the rounding gives away goes to the compilation both those neighbours
 * are of. Two cells of one compilation that touch only at a corner are joined (of two such pairs at one crossing, the
 * earlier compilation's). The outline of a compilation is then the band of its line inside its pieces along the pieces of
 * the others: its outer edge on the centre of the gap, every arc concentric with a corner of a card.
 */
export function outlinePieces(geometry: Geometry, groupAt: (row: number, col: number) => number): Piece[] {
  const { x0, y0, pitchX, pitchY, w, h, radius: r, rows, cols } = geometry
  const gx = (pitchX - w) / 2
  const gy = (pitchY - h) / 2
  const group = (row: number, col: number): number =>
    row < 0 || col < 0 || row >= rows || col >= cols ? -1 : groupAt(row, col)
  const rounded = new Set<string>()
  const pieces: { row: number; col: number; k: number; owner: number }[] = []
  for (let row = -1; row < rows; row++) {
    for (let col = -1; col < cols; col++) {
      const tl = group(row, col)
      const tr = group(row, col + 1)
      const bl = group(row + 1, col)
      const br = group(row + 1, col + 1)
      let first = tl >= 0 && tl === br && tr !== tl && bl !== tl
      let second = tr >= 0 && tr === bl && tl !== tr && br !== tr
      if (first && second) {
        if (tl < tr) second = false
        else first = false
      }
      // (row, col, corner of the cell at this crossing: 0 TL, 1 TR, 2 BR, 3 BL), its group, its two neighbours, its join
      const quads: [number, number, number, number, number, number, boolean][] = [
        [row, col, 2, tl, tr, bl, first], [row, col + 1, 3, tr, tl, br, second],
        [row + 1, col, 1, bl, tl, br, second], [row + 1, col + 1, 0, br, tr, bl, first],
      ]
      for (const [qr, qc, k, q, one, other, joined] of quads) {
        if (one === q || other === q || (joined && q >= 0)) continue
        rounded.add(`${qr},${qc},${k}`)
        pieces.push({ row: qr, col: qc, k, owner: one === other ? one : -1 })
      }
    }
  }
  const corner = (row: number, col: number, k: number) => {
    const x = x0 + col * pitchX
    const y = y0 + row * pitchY
    return [
      { in: [x - gx, y + r], out: [x + r, y - gy], p: [x - gx, y - gy] },
      { in: [x + w - r, y - gy], out: [x + w + gx, y + r], p: [x + w + gx, y - gy] },
      { in: [x + w + gx, y + h - r], out: [x + w - r, y + h + gy], p: [x + w + gx, y + h + gy] },
      { in: [x + r, y + h + gy], out: [x - gx, y + h - r], p: [x - gx, y + h + gy] },
    ][k]
  }
  const pt = (point: number[]) => `${round(point[0])} ${round(point[1])}`
  const arc = (point: number[]) => `A ${round(r + gx)} ${round(r + gy)} 0 0 1 ${pt(point)}`
  const tile = (row: number, col: number): string => {
    const isRounded = (k: number) => rounded.has(`${row},${col},${k}`)
    const start = corner(row, col, 0)
    let d = `M ${pt(isRounded(0) ? start.out : start.p)}`
    for (const k of [1, 2, 3, 0]) {
      const each = corner(row, col, k)
      d += isRounded(k) ? ` L ${pt(each.in)} ${arc(each.out)}` : ` L ${pt(each.p)}`
    }
    return `${d} Z`
  }
  const found: Piece[] = []
  for (let row = -1; row <= rows; row++) {
    for (let col = -1; col <= cols; col++) found.push({ row, col, owner: group(row, col), d: tile(row, col) })
  }
  for (const piece of pieces) {
    const each = corner(piece.row, piece.col, piece.k)
    found.push({ row: piece.row, col: piece.col, owner: piece.owner, d: `M ${pt(each.in)} ${arc(each.out)} L ${pt(each.p)} Z` })
  }
  return found
}

const round = (value: number): number => Math.round(value * 100) / 100
