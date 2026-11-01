// The output of a series as the window reads it: episodes joined from the layout, references, levers and voice-overs.
// Pure functions only: no network, no interface and no texts, so the window words everything it shows.
import { compareNames } from "./collate"
import type { components } from "../api/schema"
import type { VoiceDub } from "../stores/voices"

type Schemas = components["schemas"]

export type Reference = Schemas["Reference"]
export type Row = Schemas["CompositionRow"]
export type Lever = Schemas["EffectiveLever"]

// --- the levers and their targets ------------------------------------------------------------------------------------

/** The levers by the names the core keeps them under; a window and a command spell a lever only through these. */
export const LEVER = {
  choice: "reference.choice",
  allow: "reference.allow",
  take: "voice.take",
  player: "voice.player",
} as const

/** The target of a lever that concerns one player of one source. */
export function playerTarget(sourceId: number, player: string): string {
  return `source:${sourceId}/player:${player}`
}

/** The target of a lever that concerns one voice-over group. */
export function groupTarget(name: string): string {
  return `group:${name}`
}

// Only canonical ids parse: "007" must not read back as 7, or the target would not be the one it was made from.
const PLAYER_TARGET = /^source:(0|-?[1-9]\d*)\/player:([\s\S]*)$/
const GROUP_TARGET = /^group:([\s\S]*)$/

/** The source and the player a player target names, or null for a target of another kind. */
export function parsePlayerTarget(target: string): { sourceId: number; player: string } | null {
  const match = PLAYER_TARGET.exec(target)
  return match ? { sourceId: Number(match[1]), player: match[2] } : null
}

/** The group a group target names, or null for a target of another kind. */
export function parseGroupTarget(target: string): string | null {
  const match = GROUP_TARGET.exec(target)
  return match ? match[1] : null
}

// --- episodes --------------------------------------------------------------------------------------------------------

export interface EpisodeView {
  season: number
  episode: number
  references: Reference[]
  rows: Row[]
  levers: Lever[]
  dubs: VoiceDub[]
}

type Slot = { season: number; episode: number }

const slotKey = (slot: Slot): string => `${slot.season}:${slot.episode}`
// Names may hold any character, so a JSON array keeps the parts apart where a separator could not.
const keyOf = (...parts: (string | number)[]): string => JSON.stringify(parts)

function addTo<K, T>(groups: Map<K, T[]>, key: K, item: T): void {
  const list = groups.get(key)
  if (list) list.push(item)
  else groups.set(key, [item])
}

interface Span {
  season: number
  start: number
  end: number
}

// Excluded lines and lines with no season or no number are out of the layout; a missing end is one episode.
function spanOf(row: Row): Span | null {
  if (row.excluded || typeof row.season !== "number" || typeof row.episode_start !== "number") return null
  const end = typeof row.episode_end === "number" ? row.episode_end : row.episode_start
  return { season: row.season, start: row.episode_start, end }
}

function isSingle(row: Row): boolean {
  const span = spanOf(row)
  return span !== null && span.start === span.end
}

const bySlot = (a: Slot, b: Slot): number => a.season - b.season || a.episode - b.episode
const byEpisode = (a: EpisodeView, b: EpisodeView): number => a.episode - b.episode

// The whole episode, with no part, comes before its parts.
function byPart(a: Reference, b: Reference): number {
  const x = a.part ?? null
  const y = b.part ?? null
  if (x === null || y === null) return Number(x !== null) - Number(y !== null)
  return x - y
}

/** Every episode the layout, the references or the settings name, in order; voice-overs join episodes, never name them. */
export function joinEpisodes(
  rows: Row[],
  references: Reference[],
  settings: Schemas["EpisodeSettings"][],
  dubs: VoiceDub[],
): EpisodeView[] {
  const slots = new Map<string, Slot>()
  const note = (slot: Slot): void => {
    slots.set(slotKey(slot), { season: slot.season, episode: slot.episode })
  }

  const rowsBy = new Map<string, Row[]>()
  for (const row of rows) {
    const span = spanOf(row)
    if (!span) continue
    for (let episode = span.start; episode <= span.end; episode++) {
      const slot = { season: span.season, episode }
      note(slot)
      addTo(rowsBy, slotKey(slot), row)
    }
  }

  const referencesBy = new Map<string, Reference[]>()
  for (const reference of references) {
    note(reference)
    addTo(referencesBy, slotKey(reference), reference)
  }

  const settingsBy = new Map<string, Schemas["EpisodeSettings"]>()
  for (const each of settings) {
    note(each)
    settingsBy.set(slotKey(each), each)
  }

  const dubsBy = new Map<string, VoiceDub[]>()
  for (const dub of dubs) {
    note(dub) // a voice-over of an episode that no row names is still shown, not lost
    addTo(dubsBy, slotKey(dub), dub)
  }

  return [...slots.values()].sort(bySlot).map(({ season, episode }) => {
    const key = slotKey({ season, episode })
    return {
      season,
      episode,
      references: (referencesBy.get(key) ?? []).sort(byPart),
      rows: rowsBy.get(key) ?? [],
      levers: settingsBy.get(key)?.levers ?? [],
      dubs: dubsBy.get(key) ?? [],
    }
  })
}

/** The episodes grouped by season; seasons and episodes come in ascending order. */
export function bySeason(views: EpisodeView[]): { season: number; episodes: EpisodeView[] }[] {
  const seasons = new Map<number, EpisodeView[]>()
  for (const view of views) addTo(seasons, view.season, view)
  return [...seasons]
    .sort(([a], [b]) => a - b)
    .map(([season, episodes]) => ({ season, episodes: episodes.sort(byEpisode) }))
}

// --- the choices a window offers for an episode ----------------------------------------------------------------------

export interface PlayerOption {
  sourceId: number
  player: string
  playerLabel: string
}

export interface ReferenceOption extends PlayerOption {
  group: string
  height: number | null
}

// A label belongs to the player, not to one line, so the first non-empty one of the whole episode is taken.
function labelOf(rows: Row[], sourceId: number, player: string): string {
  const named = rows.find((row) => row.source_id === sourceId && row.player === player && row.player_label)
  return named?.player_label || player
}

const byPlayer = (a: PlayerOption, b: PlayerOption): number =>
  a.sourceId - b.sourceId || compareNames(a.player, b.player)

function playersOf(view: EpisodeView, rows: Row[]): PlayerOption[] {
  const pairs = new Map<string, PlayerOption>()
  for (const row of rows) {
    const key = keyOf(row.source_id, row.player)
    if (pairs.has(key)) continue
    pairs.set(key, {
      sourceId: row.source_id,
      player: row.player,
      playerLabel: labelOf(view.rows, row.source_id, row.player),
    })
  }
  return [...pairs.values()].sort(byPlayer)
}

/** The distinct source and player pairs the episode has lines for. */
export function sourcePlayers(view: EpisodeView): PlayerOption[] {
  return playersOf(view, view.rows)
}

/** The voice-over labels of the episode, alphabetically. */
export function voiceGroups(view: EpisodeView): string[] {
  return [...new Set(view.rows.map((row) => row.voice_label))].sort(compareNames)
}

/** The players that carry the voice-over of the given label. */
export function playerOptions(view: EpisodeView, group: string): PlayerOption[] {
  return playersOf(view, view.rows.filter((row) => row.voice_label === group))
}

// A compilation is no file of one episode, and a line that is not playable cannot be referenced.
export function referenceOptions(view: EpisodeView): ReferenceOption[] {
  const found = new Map<string, { group: string; sourceId: number; player: string; heights: number[] }>()
  for (const row of view.rows) {
    if (row.state !== "playable" || !isSingle(row)) continue
    const key = keyOf(row.voice_label, row.source_id, row.player)
    const option = found.get(key) ?? { group: row.voice_label, sourceId: row.source_id, player: row.player, heights: [] }
    option.heights.push(...(row.heights ?? []))
    found.set(key, option)
  }
  return [...found.values()]
    .map(({ heights, ...pair }) => ({
      ...pair,
      playerLabel: labelOf(view.rows, pair.sourceId, pair.player),
      height: heights.length > 0 ? Math.max(...heights) : null,
    }))
    .sort((a, b) => compareNames(a.group, b.group) || byPlayer(a, b))
}

// --- the values of the levers ----------------------------------------------------------------------------------------

export interface LeverState {
  value: unknown
  level: Schemas["Level"]
}

// The core's defaults: a reference may be used and a voice-over is taken until a level says otherwise.
function defaultValue(lever: string): unknown {
  return lever === LEVER.allow || lever === LEVER.take ? true : null
}

/** The value of a lever for a target and the level it comes from; no record means the default of the lever. */
export function leverState(levers: Lever[], lever: string, target: string | null | undefined): LeverState {
  const wanted = target ?? null
  const found = levers.find((each) => each.lever === lever && (each.target ?? null) === wanted)
  return found ? { value: found.value, level: found.level } : { value: defaultValue(lever), level: "default" }
}

/** Whether the level itself sets the lever, not merely inherits it from the level above. */
export function isOwn(state: LeverState, level: "series" | "season" | "episode"): boolean {
  return state.level === level
}

// --- the bodies of the commands --------------------------------------------------------------------------------------

/** The episode or the season a command is about; a field that is not set is left out of the body. */
export interface Scope {
  season?: number
  episode?: number
}

function scopeFields(scope: Scope): Scope {
  return {
    ...(scope.season === undefined ? {} : { season: scope.season }),
    ...(scope.episode === undefined ? {} : { episode: scope.episode }),
  }
}

/** The body of `composition.settings.set`: the scope, the lever, its target (null for none) and the value. */
export function setBody(scope: Scope, lever: string, target: string | null, value: unknown): Schemas["OutputSettingsSet"] {
  return { ...scopeFields(scope), lever, target, value }
}

/** The body of `composition.settings.reset`; a lever or a target is named only when it is given. */
export function resetBody(scope: Scope, lever?: string, target?: string | null): Schemas["OutputSettingsReset"] {
  return {
    ...scopeFields(scope),
    ...(lever === undefined ? {} : { lever }),
    ...(target === undefined ? {} : { target }),
  }
}

// --- deviations ------------------------------------------------------------------------------------------------------

/** Whether the user switched the episode off: every slot of it carries the mark `off`. */
export function isOff(view: EpisodeView): boolean {
  return view.references.length > 0 && view.references.every((reference) => reference.mark === "off")
}

/** Whether a whole level is switched off: it has episodes and every one of them is off. */
export function allOff(views: EpisodeView[]): boolean {
  return views.length > 0 && views.every(isOff)
}

/**
 * Whether the episode departs from the norm: lines without any reference, a reference not taken from its level,
 * a lever set on the episode itself, or a voice-over in error. An episode the user switched off is their choice,
 * not a departure.
 */
export function isDeviation(view: EpisodeView): boolean {
  if (isOff(view)) return false
  if (view.rows.length > 0 && view.references.length === 0) return true
  return view.references.some((ref) => ref.mark !== "by_level")
    || view.levers.some((lever) => lever.level === "episode")
    || view.dubs.some((dub) => dub.state === "error")
}

/** The names the files of one episode get, in the order of their parts (the whole episode first), each once. */
export function fileNamesOf(
  rows: { season?: number | null; episode?: number | null; part?: number | null; new_filename_preview?: string | null }[],
  season: number,
  episode: number,
): string[] {
  const own = rows.filter((row) => row.season === season && row.episode === episode && row.new_filename_preview)
  own.sort((a, b) => Number(a.part != null) - Number(b.part != null) || (a.part ?? 0) - (b.part ?? 0))
  return [...new Set(own.map((row) => row.new_filename_preview as string))]
}

/** The reference of one part of the episode; no part names the whole episode, and null when there is none. */
export function referenceOf(view: EpisodeView, part: number | null = null): Reference | null {
  return view.references.find((reference) => (reference.part ?? null) === part) ?? null
}

/** One view for a set of episodes (a season, a whole series): the rows of all of them once each, so that the choices of
 *  a lever offered on a level are those any episode of the level could take. */
export function scopeView(views: EpisodeView[]): EpisodeView {
  const seen = new Set<string>()
  const rows: Row[] = []
  for (const view of views) {
    for (const row of view.rows) {
      const key = `${row.source_id}/${row.row_key}`
      if (seen.has(key)) continue
      seen.add(key)
      rows.push(row)
    }
  }
  return { season: 0, episode: 0, references: [], rows, levers: [], dubs: [] }
}
