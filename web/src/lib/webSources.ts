// The sources of a web release as the add form and the series properties draw them: a row per player, in the
// order the provider tries them.
export const SOURCE_ORDER = ["vk", "allvideo", "vibix"] as const
export type SourceKey = (typeof SOURCE_ORDER)[number]

export interface PlayerReport {
  present: boolean
  available: boolean | null
  reason?: string
  enabled?: boolean
  toggle?: string
}

export interface ResolutionGroup {
  resolution: number
  ranges: string
  count: number
}

export interface LayoutSource {
  url: string
  studio: string
  ranges: string
  count: number
  resolutions?: ResolutionGroup[]
}

/** The catalog of a shared source (Vibix): its seasons, and the one the address chose. */
export interface SourcesLayout {
  sources: LayoutSource[]
  selected_source?: string | null
  reason?: string
}

export interface EpisodeSpan {
  count: number
  ranges: string
}

export interface PlayerCoverage {
  present: number
  available: number
  count: number
  ranges: string
  resolutions?: ResolutionGroup[]
  /** Episodes the player shows but does not play. */
  unavailable?: EpisodeSpan
  /** Episodes whose answer stayed unsure to the end: not the same as "no". */
  not_obtained?: EpisodeSpan
}

/** What the players of a page really give, read from every episode. */
export interface SourcesCoverage {
  players: Record<string, PlayerCoverage>
  reason?: string
}

export interface SourcesState {
  players: Record<string, PlayerReport>
  layout: SourcesLayout | null
  coverage: SourcesCoverage | null
  layoutLoading: boolean
  coverageLoading: boolean
}

export type SourceState = "off" | "absent" | "pending" | "available" | "unavailable" | "reading" | "choose"

export interface SeasonOption {
  url: string
  name: string
  ranges: string
  count: number
}

export type GapKind = "unavailable" | "notObtained"

export interface SourceGap {
  kind: GapKind
  ranges: string
}

export interface SourceRow {
  key: SourceKey
  enabled: boolean
  /** Address that flips this player on or off; the form re-parses it. */
  toggle: string
  state: SourceState
  /** Episodes the source gives; null while there is nothing to count. */
  count: number | null
  /** Resolution of the episodes, grouped by ranges. */
  groups: { label: string; ranges: string }[]
  /** Episodes the player shows but does not give, and those whose answer was not obtained, each as ranges. */
  gaps: SourceGap[]
  seasons: SeasonOption[]
  selected: string
}

const groupsOf = (resolutions: ResolutionGroup[] | undefined) =>
  (resolutions ?? []).map((g) => ({ label: `${g.resolution}p`, ranges: g.ranges }))

function gapsOf(given: PlayerCoverage): SourceGap[] {
  const gaps: SourceGap[] = []
  if (given.unavailable?.count) gaps.push({ kind: "unavailable", ranges: given.unavailable.ranges })
  if (given.not_obtained?.count) gaps.push({ kind: "notObtained", ranges: given.not_obtained.ranges })
  return gaps
}

function reasonCode(reason: string | undefined): string {
  try {
    return String(JSON.parse(reason ?? "").code ?? "")
  } catch {
    return ""
  }
}

function vibixRow(row: SourceRow, layout: SourcesLayout | null, loading: boolean): SourceRow {
  if (loading || layout === null) return { ...row, state: loading ? "reading" : "pending" }
  const seasons = layout.sources.map((s) => ({ url: s.url, name: s.studio, ranges: s.ranges, count: s.count }))
  if (!seasons.length) return { ...row, state: reasonCode(layout.reason) === "page_no_player" ? "absent" : "unavailable" }
  const chosen = layout.sources.find((s) => s.url === layout.selected_source)
  if (!chosen) return { ...row, state: "choose", seasons }
  return { ...row, state: "available", seasons, selected: chosen.url, count: chosen.count, groups: groupsOf(chosen.resolutions) }
}

/** A page player: what the episodes gave once they are read, else the preview of the release page. */
function pageRow(row: SourceRow, report: PlayerReport, state: SourcesState): SourceRow {
  if (state.coverageLoading) return { ...row, state: "reading" }
  const given = state.coverage?.players[row.key]
  if (given) {
    if (given.present === 0) return { ...row, state: "absent" }
    if (given.count === 0) return { ...row, state: "unavailable", gaps: gapsOf(given) }
    return { ...row, state: "available", count: given.count, groups: groupsOf(given.resolutions), gaps: gapsOf(given) }
  }
  if (!report.present) return { ...row, state: "absent" }
  return { ...row, state: report.available === true ? "available" : report.available === false ? "unavailable" : "pending" }
}

export function sourceRows(state: SourcesState): SourceRow[] {
  return SOURCE_ORDER.filter((key) => key in state.players).map((key) => {
    const report = state.players[key]
    const enabled = report.enabled !== false
    const row: SourceRow = { key, enabled, toggle: report.toggle ?? "", state: "pending", count: null, groups: [], gaps: [], seasons: [], selected: "" }
    if (!enabled) return { ...row, state: "off" }
    return key === "vibix" ? vibixRow(row, state.layout, state.layoutLoading) : pageRow(row, report, state)
  })
}

/** The form waits while a shared catalog still has to be read or a season chosen: a series added then would scan a guess. */
export function blocksAdd(rows: SourceRow[]): boolean {
  return rows.some((row) => row.key === "vibix" && (row.state === "reading" || row.state === "choose"))
}
