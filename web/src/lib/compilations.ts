import type { PlanCut } from "./plan"
import type { components } from "../api/schema"
import type { CutOf } from "../composables/outputCalls"
import type { EpisodeStatus, EpisodeView } from "./output"

type Schemas = components["schemas"]

/** A compilation of a season and the episodes it covers, as `composition` gives it (stage 12). */
export type CompilationView = Schemas["CompilationView"]
/** A cut of an episode out of its compilation, as `voices` tells it. */
export type CutItem = PlanCut

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

/** The height of the reference an episode has now (its single video), null when not known; `off` when the user switched the
 *  episode off (it takes nothing, from a compilation neither). */
export type ReferenceHeight = (season: number, episode: number) => number | null | "off"

/** Whether the episode is cut out of the compilation: it stands in it and the compilation is higher than the reference
 *  the episode has now — which may be a video held on disk that the layout does not show (the same rule as the choice of
 *  the reference, scan/reference.py). */
export function takes(view: CompilationView, episode: number, height: ReferenceHeight): boolean {
  const each = (view.episodes ?? []).find((one) => one.episode === episode)
  if (!each?.member) return false
  const own = height(view.season, episode)
  if (own === "off") return false
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

/** The state of an episode while it is being cut out of its compilation: the step of its cut, which outranks its tracks
 *  (the video they go with is not there yet); null when no cut is under way (none, or its video is made). */
export function cutOverride(cut: CutItem | undefined): EpisodeStatus | null {
  if (!cut) return null
  const state = CUT_STATES[cut.status]
  return state === undefined || state === "ready" ? null : state
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
