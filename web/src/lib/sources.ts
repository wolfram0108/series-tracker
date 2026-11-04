// The sources of a series as the add window and the series properties draw them: one group per source, its players,
// their voice-overs. Nothing here computes an episode, a season, a voice-over or a resolution — those are the rows the
// rules of the assigned profile made (the layout); the ranges below only join the numbers of the rows that follow one
// another. A source without such rows shows no line of episodes or voice-overs.
import type { components } from "../api/schema"
import { ru } from "../i18n/ru"
import { t, tn, type MessageKey } from "../i18n"
import type { PageProgress, PageStatus } from "../stores/sourceTables"

type Schemas = components["schemas"]

export type SourceSummary = Schemas["SourceSummary"]
export type PlayerSummary = Schemas["PlayerSummary"]
export type SeriesSource = Schemas["SeriesSource"]
export type SourceDraft = Schemas["SourceDraft"]
export type SourceType = Schemas["SourceType"]
export type PreviewReply = Schemas["CompositionPreviewReply"]
export type TableState = Schemas["TableState"]
export type ResolveReply = Schemas["SiteResolveReply"]
export type LayoutRow = Schemas["CompositionRow"]

/** The player whose list holds several seasons of a catalog; the user picks the one that belongs to the source. */
export const CATALOG_PLAYER = "vibix"
/** The site of a VK channel is the only one whose source is of the type `vk_video`; any other site is `web_video`. */
const CHANNEL_SITE = "vk_channel"
const VK_MODES = ["search", "get_all"] as const
export type VkMode = (typeof VK_MODES)[number]

export type SourceState = "reading" | "failed" | "absent" | "waiting" | "noProfile" | "ready"

export interface PlayerView {
  player: string
  title: string
  /** How many rows the page gave the player; a count of the table, not an episode. */
  rows: number
  /** How many groups the player keeps (the seasons of a catalog): only the choice of a catalog season needs it. */
  groups: number
  /** The episodes the constructor took from the player, with the gaps left in, one line to a season: `1-50 (480p-2160p), 61-120`. */
  coverage: string[]
  /** One line to a voice-over: its episodes, its name and its group (or `Нет`); both lists come from the rows of the constructor. */
  voiceLines: string[]
  off: boolean
  /** The player shows a catalog of several seasons, so the source may name the one it takes. */
  chooseSeason: boolean
}

export interface SourceView {
  state: SourceState
  /** Coded reason of a failure; worded by `reasonText`. */
  reason: string | null
  progress: PageProgress | null
  players: PlayerView[]
}

/** What a source window item is, whether it is a draft of the add window or a source of a stored series. */
export interface SourceItem {
  id: number | string
  url: string
  sourceType: SourceType
  profileId: number | null
  vkSearchMode: string
  vibixSeason: number | null
  playersOff: string[]
  /** The name of the voice-over of the lines that have none; empty when the user gave none. */
  voiceName: string
  view: SourceView
}

// --- the view of one source ------------------------------------------------------------------------------------------

// --- the episodes of a source as the constructor numbered them ---------------------------------------------------------------

interface Span {
  from: number
  to: number
  low: number | null
  high: number | null
}

/** The rows that carry an episode of the constructor and that it has not left out. */
function numbered(rows: LayoutRow[]): LayoutRow[] {
  return rows.filter((row) => !row.excluded && row.episode_start != null)
}

const lowest = (a: number | null, b: number | null): number | null => (a === null ? b : b === null ? a : Math.min(a, b))
const highest = (a: number | null, b: number | null): number | null => (a === null ? b : b === null ? a : Math.max(a, b))

/** The rows of one season as ranges: a row that begins inside the range or right after it joins it, a gap starts a new one. */
function spansOf(rows: LayoutRow[]): Span[] {
  const spans: Span[] = []
  for (const row of [...rows].sort((a, b) => (a.episode_start ?? 0) - (b.episode_start ?? 0))) {
    const from = row.episode_start as number
    const to = row.episode_end ?? from
    const heights = row.heights ?? []
    const low = heights.length ? Math.min(...heights) : null
    const high = heights.length ? Math.max(...heights) : null
    const last = spans[spans.length - 1]
    if (last && from <= last.to + 1) {
      last.to = Math.max(last.to, to)
      last.low = lowest(last.low, low)
      last.high = highest(last.high, high)
    } else {
      spans.push({ from, to, low, high })
    }
  }
  return spans
}

function spanText(span: Span, withSteps: boolean): string {
  const range = span.from === span.to ? `${span.from}` : `${span.from}-${span.to}`
  if (!withSteps || span.low === null || span.high === null) return range
  const low = t("sources.height.step", { height: span.low })
  const steps = span.low === span.high ? low : `${low}-${t("sources.height.step", { height: span.high })}`
  return t("sources.range.steps", { range, steps })
}

/** The ranges of the rows, a line to a season (a season is named only when there are several). */
function rangeLines(rows: LayoutRow[], withSteps: boolean): string[] {
  const seasons = new Map<number, LayoutRow[]>()
  for (const row of numbered(rows)) seasons.set(row.season ?? 0, [...(seasons.get(row.season ?? 0) ?? []), row])
  const ordered = [...seasons.entries()].sort(([a], [b]) => a - b)
  return ordered.map(([season, group]) => {
    const text = spansOf(group).map((span) => spanText(span, withSteps)).join(", ")
    return ordered.length > 1 && season > 0 ? t("sources.range.season", { season, ranges: text }) : text
  })
}

/** The episodes of a player, with the gaps the constructor left, and the resolution steps of each range. */
export function coverageOf(rows: LayoutRow[]): string[] {
  return rangeLines(rows, true)
}

const groupText = (group: string | null | undefined): string =>
  group ? t("sources.voice.group", { group }) : t("sources.voice.noGroup")

/** A line to a voice-over: its episodes, its name (or the label of the layout when it has none) and its group. */
export function voiceLinesOf(rows: LayoutRow[]): string[] {
  const voices = new Map<string, { name: string; group: string | null; rows: LayoutRow[] }>()
  for (const row of numbered(rows)) {
    const name = row.voice_name.trim() || row.voice_label
    const key = `${name}\u0000${row.voice_group ?? ""}`
    const known = voices.get(key) ?? { name, group: row.voice_group || null, rows: [] }
    known.rows.push(row)
    voices.set(key, known)
  }
  return [...voices.values()]
    .map((voice) => ({ voice, first: Math.min(...voice.rows.map((row) => row.episode_start as number)) }))
    .sort((a, b) => a.first - b.first || a.voice.name.localeCompare(b.voice.name))
    .map(({ voice }) => t("sources.voice.line", {
      ranges: rangeLines(voice.rows, false).join("; "), name: voice.name, group: groupText(voice.group),
    }))
}

function playerView(player: PlayerSummary, playersOff: string[], layout: LayoutRow[]): PlayerView {
  const rows = layout.filter((row) => row.player === player.player)
  return {
    player: player.player,
    // The label comes from the table row (`player_label`): the window keeps no map of players.
    title: player.player_label || player.player,
    rows: player.rows,
    groups: player.groups,
    // The lines are built from the rows the constructor made by the rules of the assigned profile and from nothing else:
    // without a profile (no rows of the layout) there is no line, and the card tells to assign one.
    coverage: coverageOf(rows),
    voiceLines: voiceLinesOf(rows),
    off: playersOff.includes(player.player),
    chooseSeason: player.player === CATALOG_PLAYER && player.groups > 1,
  }
}

export interface ViewInput {
  summary: SourceSummary | null
  playersOff: string[]
  /** The rows of the layout that belong to this source: the numbers come from the constructor's answer only. */
  rows?: LayoutRow[]
  /** The page as the core's events last said (progress, ready, failed); wins over the stored summary. */
  live?: PageStatus | null
  /** The answer of the last preview request, for a draft that is no source of a series yet. */
  asked?: { state: TableState; reason?: string | null } | null
}

/** One state for the source: the newest news of its page, else what the request or the summary said. */
export function viewOf(input: ViewInput): SourceView {
  const { summary, live, asked } = input
  const players = (summary?.players ?? []).map((player) => playerView(player, input.playersOff, input.rows ?? []))
  const settled = (state: SourceState, reason: string | null = null): SourceView =>
    ({ state, reason, progress: null, players })
  if (live?.state === "reading") return { state: "reading", reason: null, progress: live.progress, players }
  if (live?.state === "failed") return settled("failed", live.reason)
  if (asked?.state === "reading") return settled("reading")
  if (asked?.state === "failed") return settled("failed", asked.reason ?? summary?.reason ?? null)
  if (asked?.state === "absent" && !summary) return settled("absent")
  switch (summary?.status) {
    case "failed": return settled("failed", summary.reason ?? null)
    case "no_profile": return settled("noProfile")
    case "ready": return settled("ready")
    default: return settled("waiting")
  }
}

// --- texts -----------------------------------------------------------------------------------------------------------

export function phaseText(phase: string): string {
  const key = `sources.phase.${phase}`
  return key in ru ? t(key as MessageKey) : t("sources.phase.unknown")
}

/** The site of a page by the address the core keys its table with (a VK channel adds "|name|mode" to its address). */
export function pageHost(url: string): string {
  try {
    return new URL(url.split("|")[0]).host
  } catch {
    return url
  }
}

/** The phase, how far it is and how fast (Р-36); a read that does not know its total yet says only the phase. */
export function progressText(progress: PageProgress): string {
  const phase = phaseText(progress.phase)
  if (progress.total <= 0) return phase
  const values = { phase, current: progress.current, total: progress.total }
  return progress.rate != null
    ? t("sources.progress.rate", { ...values, rate: progress.rate.toFixed(1) })
    : t("sources.progress", values)
}

/** Players and rows of a source, with plural forms, for its head. */
export function sourceCaption(players: PlayerView[]): string {
  const rows = players.reduce((sum, player) => sum + player.rows, 0)
  return t("sources.caption.source", { players: tn("sources.count.players", players.length), rows: tn("sources.count.rows", rows) })
}

/** The rows the page gave a player: a count of the table, never a season or a resolution. */
export function playerCaption(player: PlayerView): string {
  return tn("sources.count.rows", player.rows)
}

// --- the choices that belong to a source -----------------------------------------------------------------------------

/** The disabled players after the switch of one; a player that is on again leaves the list. */
export function togglePlayer(off: string[], player: string): string[] {
  return off.includes(player) ? off.filter((each) => each !== player) : [...off, player]
}

/** Positions of the catalog seasons (counted from 1, as `vibix_season` is) among which the user picks. */
export function seasonPositions(groups: number): number[] {
  return Array.from({ length: groups }, (_, index) => index + 1)
}

// --- addresses -------------------------------------------------------------------------------------------------------

/** The type of the source an address makes, from the site that reads it; null: no site does, so it is no video source. */
export function sourceTypeOf(reply: ResolveReply): SourceType | null {
  if (!reply.site) return null
  return reply.site === CHANNEL_SITE ? "vk_video" : "web_video"
}

/** Whether the reading of a page ended after the answer in hand was taken: that answer said `reading`, and the news of
 *  the page is a result later than the one the answer already knew. Such news asks the core again, reading nothing. */
export function readEnded(asked: { state: TableState } | null, news: PageStatus | null, handled: PageStatus | null): boolean {
  return asked?.state === "reading" && news !== null && news !== handled && news.state !== "reading"
}

export function vkModeOf(value: string | null | undefined): VkMode {
  return VK_MODES.find((mode) => mode === value) ?? "search"
}

/** The channel and the query of a VK source address (`channel|query`); a typed channel link with `?q=` is split too. */
export function splitVkAddress(address: string): { channel: string; query: string } {
  const [channel, ...rest] = address.split("|")
  if (rest.length) return { channel: channel.trim(), query: rest[0].trim() }
  try {
    const link = new URL(address.trim())
    const query = link.searchParams.get("q") ?? ""
    return { channel: `${link.protocol}//${link.hostname}${link.pathname}`, query }
  } catch {
    return { channel: address.trim(), query: "" }
  }
}

export function joinVkAddress(channel: string, query: string): string {
  return `${channel.trim().replace(/\\/g, "/")}|${query.trim()}`
}

/** The host of a source address (the channel part of a VK address), as the series keeps it in `site`. */
export function siteOf(address: string): string {
  try {
    return new URL(address.split("|")[0].trim()).hostname.replace(/^www\./, "")
  } catch {
    return ""
  }
}

/** The choices that change what the site is asked for; the same ones the core reads from the source. */
export function readOptions(source: { sourceType: SourceType; vkSearchMode: string }): Record<string, string> {
  return source.sourceType === "vk_video" ? { vk_search_mode: source.vkSearchMode } : {}
}

/** The body of a new source for `POST /api/series` and `POST /api/series/{id}/sources`. */
export function draftBody(item: Pick<SourceItem, "url" | "sourceType" | "profileId" | "vkSearchMode" | "vibixSeason" | "playersOff" | "voiceName">): SourceDraft {
  return {
    url: item.url,
    source_type: item.sourceType,
    parser_profile_id: item.profileId,
    vk_search_mode: item.vkSearchMode,
    vibix_season: item.vibixSeason,
    players_off: item.playersOff,
    voice_name: item.voiceName,
  }
}

export function itemOfSource(
  source: SeriesSource, summary: SourceSummary | null, live: PageStatus | null, layout: LayoutRow[] = [],
): SourceItem {
  const playersOff = source.players_off ?? []
  return {
    id: source.id,
    url: source.url,
    sourceType: source.source_type,
    profileId: source.parser_profile_id ?? null,
    vkSearchMode: source.vk_search_mode,
    vibixSeason: source.vibix_season ?? null,
    playersOff,
    voiceName: source.voice_name ?? "",
    view: viewOf({ summary, playersOff, live, rows: layout.filter((row) => row.source_id === source.id) }),
  }
}

/** The summary of a stored source in the answer of `composition.get`, found by its id. */
export function summaryOf(summaries: SourceSummary[], sourceId: number): SourceSummary | null {
  return summaries.find((summary) => summary.source_id === sourceId) ?? null
}

export interface ProfileOption {
  label: string
  value: number | null
}

export function profileOptions(profiles: { id: number; name: string }[]): ProfileOption[] {
  return [{ label: t("sources.profile.none"), value: null }, ...profiles.map((profile) => ({ label: profile.name, value: profile.id }))]
}
