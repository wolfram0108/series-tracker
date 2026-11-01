// The sources of a series as the add window and the series properties draw them: one group per source, its players,
// their voice-overs. Nothing here computes an episode or a season number — those are the constructor's alone; the
// window shows counts, resolution steps and voice-overs with their groups, and the numbers only through the answer.
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

/** The player whose list holds several seasons of a catalog; the user picks the one that belongs to the source. */
export const CATALOG_PLAYER = "vibix"
/** The site of a VK channel is the only one whose source is of the type `vk_video`; any other site is `web_video`. */
const CHANNEL_SITE = "vk_channel"
const VK_MODES = ["search", "get_all"] as const
export type VkMode = (typeof VK_MODES)[number]

export type SourceState = "reading" | "failed" | "absent" | "waiting" | "noProfile" | "ready"

export interface VoiceView {
  name: string
  /** The user's group of the voice-over; null: it is in no group. */
  group: string | null
  rows: number
}

export interface PlayerView {
  player: string
  title: string
  rows: number
  groups: number
  topHeight: number | null
  steps: number[]
  voices: VoiceView[]
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

export function topHeight(heights: number[]): number | null {
  return heights.length ? Math.max(...heights) : null
}

function playerView(player: PlayerSummary, playersOff: string[]): PlayerView {
  const heights = player.heights ?? []
  return {
    player: player.player,
    // The label comes from the table row (`player_label`): the window keeps no map of players.
    title: player.player_label || player.player,
    rows: player.rows,
    groups: player.groups,
    topHeight: topHeight(heights),
    steps: [...heights].sort((a, b) => b - a),
    voices: (player.voices ?? []).map((voice) => ({ name: voice.name, group: voice.group ?? null, rows: voice.rows })),
    off: playersOff.includes(player.player),
    chooseSeason: player.player === CATALOG_PLAYER && player.groups > 1,
  }
}

export interface ViewInput {
  summary: SourceSummary | null
  playersOff: string[]
  /** The page as the core's events last said (progress, ready, failed); wins over the stored summary. */
  live?: PageStatus | null
  /** The answer of the last preview request, for a draft that is no source of a series yet. */
  asked?: { state: TableState; reason?: string | null } | null
}

/** One state for the source: the newest news of its page, else what the request or the summary said. */
export function viewOf(input: ViewInput): SourceView {
  const { summary, live, asked } = input
  const players = (summary?.players ?? []).map((player) => playerView(player, input.playersOff))
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

/** The phase and how far it is; a read that does not know its total yet says only the phase. */
export function progressText(progress: PageProgress): string {
  const phase = phaseText(progress.phase)
  return progress.total > 0 ? t("sources.progress", { phase, current: progress.current, total: progress.total }) : phase
}

/** Players and rows of a source, with plural forms, for its head. */
export function sourceCaption(players: PlayerView[]): string {
  const rows = players.reduce((sum, player) => sum + player.rows, 0)
  return t("sources.caption.source", { players: tn("sources.count.players", players.length), rows: tn("sources.count.rows", rows) })
}

/** Rows and seasons of a player; a player that keeps no seasons says only its rows. */
export function playerCaption(player: PlayerView): string {
  const rows = tn("sources.count.rows", player.rows)
  return player.groups > 0 ? t("sources.caption.player", { rows, groups: tn("sources.count.groups", player.groups) }) : rows
}

export function heightText(player: PlayerView): string {
  return player.topHeight === null ? "" : t("sources.height", { height: player.topHeight })
}

export function stepsText(player: PlayerView): string {
  return player.steps.map((height) => t("sources.height.step", { height })).join(", ")
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

export function itemOfSource(source: SeriesSource, summary: SourceSummary | null, live: PageStatus | null): SourceItem {
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
    view: viewOf({ summary, playersOff, live }),
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
