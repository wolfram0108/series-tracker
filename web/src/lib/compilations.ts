import type { components } from "../api/schema"
import type { CutOf } from "../composables/outputCalls"
import type { EpisodeStatus, EpisodeView } from "./output"

type Schemas = components["schemas"]

/** A compilation of a season and the episodes it covers, as `composition` gives it (stage 12). */
export type CompilationView = Schemas["CompilationView"]
/** A cut of an episode out of its compilation, as `voices` tells it. */
export type CutItem = Schemas["CutItem"]

/** How many colours compilations take before they come round again (tokens --compilation-1…16). */
export const COMPILATION_COLORS = 16

/** The letter of the n-th compilation of a season: A…Z, then AA, AB… */
export function letterOf(index: number): string {
  let n = index
  let letter = ""
  do {
    letter = String.fromCharCode(65 + (n % 26)) + letter
    n = Math.floor(n / 26) - 1
  } while (n >= 0)
  return letter
}

/** What a compilation is called and painted with in the window. */
export interface Badge {
  key: string
  letter: string
  /** 1…16: the token --compilation-N. */
  color: number
}

export const badgeOf = (view: CompilationView): Badge =>
  ({ key: view.ref.key, letter: letterOf(view.index), color: (view.index % COMPILATION_COLORS) + 1 })

const slot = (season: number, episode: number): string => `${season}:${episode}`

/** The height of the reference an episode has now (its single video), null when not known. */
export type ReferenceHeight = (season: number, episode: number) => number | null

/** Whether the episode is cut out of the compilation: it stands in it and the compilation is higher than the reference
 *  the episode has now — which may be a video held on disk that the layout does not show (the same rule as the choice of
 *  the reference, scan/reference.py). */
export function takes(view: CompilationView, episode: number, height: ReferenceHeight): boolean {
  const each = (view.episodes ?? []).find((one) => one.episode === episode)
  if (!each?.member) return false
  const own = height(view.season, episode)
  return own === null || (view.ref.height ?? 0) > own
}

/** The compilation every episode is cut out of, by "season:episode". */
export function membersOf(views: readonly CompilationView[], height: ReferenceHeight = () => null): Map<string, Badge> {
  const found = new Map<string, Badge>()
  for (const view of views) {
    for (const each of view.episodes ?? []) {
      if (takes(view, each.episode, height)) found.set(slot(view.season, each.episode), badgeOf(view))
    }
  }
  return found
}

export const memberOf = (members: Map<string, Badge>, season: number, episode: number): Badge | undefined =>
  members.get(slot(season, episode))

/** The cut of every episode, by "season:episode" (an episode in parts is not cut). */
export function cutsBySlot(cuts: readonly CutItem[]): Map<string, CutItem> {
  return new Map(cuts.filter((cut) => !cut.part).map((cut) => [slot(cut.season, cut.episode), cut] as const))
}

export const cutOf = (cuts: Map<string, CutItem>, season: number, episode: number): CutItem | undefined =>
  cuts.get(slot(season, episode))

/** The state of a cut as the colour of a card: what the episode of a compilation is at. */
export type CutState = "wait" | "down" | "align" | "ready" | "error"
const CUT_STATES: Record<string, CutState> = {
  planned: "wait", downloading: "down", ready: "wait", submitting: "align", aligning: "align", done: "ready",
  offered: "ready", failed: "error",
}
export const cutState = (cut: CutItem | undefined): CutState | null => cut ? CUT_STATES[cut.status] ?? "wait" : null

/** The video of the episode cut out of the compilation that lies in its container, or waits for its next build: what is
 *  left of a cut whose work has ended. */
export function heldCut(view: EpisodeView | undefined, key: string): CutOf | null {
  const reference = view?.references.find((each) => each.source_id != null)
  const file = (view?.files ?? []).find((each) => each.source_id === reference?.source_id && each.row_key === reference?.row_key)
  return [file?.cut, file?.pending_cut].find((each) => each?.key === key) ?? null
}

/** The card of an episode in the view «Компиляции»: off when it is not cut out of the compilation; else its cut while one
 *  is under way, ready when its video is the compilation's, waiting before anything began. */
export function cardStatus(member: boolean, cut: CutItem | undefined, held: CutOf | null = null): EpisodeStatus {
  if (!member) return "off"
  return cutState(cut) ?? (held ? "ready" : "wait")
}

/** The share of the video of a cut taken from the compilation, 0…1; null while it is not known. */
export function shareOf(cut: Pick<CutItem, "from_ref_s" | "from_dub_s"> | CutOf | null | undefined): number | null {
  const ref = cut?.from_ref_s ?? null
  const own = cut?.from_dub_s ?? null
  if (ref === null || own === null || ref + own <= 0) return null
  return ref / (ref + own)
}

// --- the outline of the compilations over the grid of cards -----------------------------------------------------------

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
