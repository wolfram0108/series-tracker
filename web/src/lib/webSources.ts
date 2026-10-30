// The sources of a web release as the add form draws them: a row per player, in the order the provider tries them.
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

export interface SourcesLayout {
  sources: LayoutSource[]
  selected_source?: string | null
  reason?: string
}

export type SourceState = "off" | "absent" | "pending" | "available" | "unavailable" | "reading" | "choose"

export interface SeasonOption {
  url: string
  name: string
  ranges: string
  count: number
}

export interface SourceRow {
  key: SourceKey
  enabled: boolean
  /** Address that flips this player on or off; the form re-parses it. */
  toggle: string
  state: SourceState
  /** Episodes of the chosen season; null while there is no single source to count. */
  count: number | null
  /** Resolution of the episodes, grouped by ranges. */
  groups: { label: string; ranges: string }[]
  seasons: SeasonOption[]
  selected: string
}

function vibixRow(row: SourceRow, layout: SourcesLayout | null, loading: boolean): SourceRow {
  if (loading || layout === null) return { ...row, state: loading ? "reading" : "pending" }
  const seasons = layout.sources.map((s) => ({ url: s.url, name: s.studio, ranges: s.ranges, count: s.count }))
  if (layout.reason || !seasons.length) return { ...row, state: "unavailable" }
  const chosen = layout.sources.find((s) => s.url === layout.selected_source)
  if (!chosen) return { ...row, state: "choose", seasons }
  return {
    ...row, state: "available", seasons, selected: chosen.url, count: chosen.count,
    groups: (chosen.resolutions ?? []).map((g) => ({ label: `${g.resolution}p`, ranges: g.ranges })),
  }
}

export function sourceRows(players: Record<string, PlayerReport>, layout: SourcesLayout | null, loading: boolean): SourceRow[] {
  return SOURCE_ORDER.filter((key) => key in players).map((key) => {
    const report = players[key]
    const enabled = report.enabled !== false
    const row: SourceRow = { key, enabled, toggle: report.toggle ?? "", state: "pending", count: null, groups: [], seasons: [], selected: "" }
    if (!enabled) return { ...row, state: "off" }
    if (!report.present) return { ...row, state: "absent" }
    if (key === "vibix") return vibixRow(row, layout, loading)
    return { ...row, state: report.available === true ? "available" : report.available === false ? "unavailable" : "pending" }
  })
}

/** The form waits while a source still has to be read or chosen: a series added then would scan a guess. */
export function blocksAdd(rows: SourceRow[]): boolean {
  return rows.some((row) => row.key === "vibix" && (row.state === "reading" || row.state === "choose"))
}
