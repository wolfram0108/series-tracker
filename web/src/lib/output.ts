// The output of a series as the window reads it: episodes joined from the layout, references, levers and voice-overs.
// Pure functions only: no network, no interface and no texts, so the window words everything it shows.
import { compareNames } from "./collate"
import type { components } from "../api/schema"
import type { VoiceDub } from "../stores/voices"
import type { MediaFile } from "../composables/outputCalls"

type Schemas = components["schemas"]

export type Reference = Schemas["Reference"]
export type Row = Schemas["CompositionRow"]
export type Lever = Schemas["EffectiveLever"]
export type LeverSpec = Schemas["LeverSpec"]

/** The forms of lever (target/shape) the window draws. The core refuses to register any other (core/contracts
 *  output_settings.py: LEVER_FORMS); a test keeps the two lists one list. A lever of another form is not hidden: the
 *  window says it cannot draw it. */
export const RENDERED_FORMS = [
  "none/variant_order", "group/variant_order", "group/flag", "variant/flag",
] as const

const RENDERED = new Set<string>(RENDERED_FORMS)

/** Whether the window draws a lever of this description. */
export const isDrawn = (spec: LeverSpec): boolean => RENDERED.has(`${spec.target}/${spec.shape}`)

// --- the targets of the levers --------------------------------------------------------------------------------------

/** The target of a lever that concerns one voice-over group. */
export function groupTarget(name: string): string {
  return `group:${name}`
}

/** The target of a lever that concerns one variant: a voice-over group at a player of a source. A JSON array, as the
 *  core makes it, so that no name can be taken for a part of another. */
export function variantTarget(group: string, sourceId: number, player: string): string {
  return `variant:${JSON.stringify([group, sourceId, player])}`
}

const GROUP_TARGET = /^group:([\s\S]*)$/

/** The group a group target names, or null for a target of another kind. */
export function parseGroupTarget(target: string): string | null {
  const match = GROUP_TARGET.exec(target)
  return match ? match[1] : null
}

/** The group, the source and the player a variant target names, or null for a target of another kind. */
export function parseVariantTarget(target: string): { group: string; sourceId: number; player: string } | null {
  if (!target.startsWith("variant:")) return null
  try {
    const found: unknown = JSON.parse(target.slice("variant:".length))
    if (!Array.isArray(found) || found.length !== 3) return null
    const [group, sourceId, player] = found
    if (typeof group !== "string" || !Number.isInteger(sourceId) || typeof player !== "string") return null
    return variantTarget(group, sourceId as number, player) === target ? { group, sourceId: sourceId as number, player } : null
  } catch {
    return null
  }
}

// --- episodes --------------------------------------------------------------------------------------------------------

export interface EpisodeView {
  season: number
  episode: number
  references: Reference[]
  rows: Row[]
  levers: Lever[]
  dubs: VoiceDub[]
  /** The files of the plan of the series: the status of the video of a reference is read from them. */
  files?: MediaFile[]
  /** The labels of the players of the sources, by `source:player`: a player the episode has no line for is still named. */
  labels?: Record<string, string>
  /** What may be done with an object in each of its states, as the core gives it with the layout. */
  availability?: Record<string, string[]>
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
  availability: Record<string, string[]> = {},
  files: MediaFile[] = [],
  labels: Record<string, string> = {},
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
      files,
      labels,
      availability,
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

// --- the variants a window offers for a level ------------------------------------------------------------------------

export interface PlayerOption {
  sourceId: number
  player: string
  playerLabel: string
}

/** A place a voice-over can be had from: a voice-over group at a player of a source, with the best height it has. */
export interface Variant extends PlayerOption {
  group: string
  height: number | null
  /** What the lines of it allow (the states of core/contracts/availability.py): `referable` when a line of it is one
   *  episode that plays (a compilation is no file of one episode), `playable` when only compilations play, else `absent`. */
  state: "referable" | "playable" | "absent"
}

/** Whether a table of what is allowed (as the core gives it with the layout) allows the action in the state. */
export function allowedIn(table: Record<string, string[]> | undefined, action: string, state: string): boolean {
  return (table?.[action] ?? []).includes(state)
}

/** Whether the table of the core allows the action in the state: a control is drawn enabled only where it does. */
export function canDo(view: EpisodeView, action: string, state: string): boolean {
  return allowedIn(view.availability, action, state)
}

/** One voice-over group and its variants: a column of the output. */
export interface VoiceColumn {
  group: string
  variants: Variant[]
}

// A label belongs to the player, not to one line, so the first non-empty one of the whole level is taken.
function labelOf(rows: Row[], sourceId: number, player: string): string {
  const named = rows.find((row) => row.source_id === sourceId && row.player === player && row.player_label)
  return named?.player_label || player
}

const byPlayer = (a: PlayerOption, b: PlayerOption): number =>
  a.sourceId - b.sourceId || compareNames(a.player, b.player)

/** The distinct source and player pairs the view has lines for. */
export function sourcePlayers(view: EpisodeView): PlayerOption[] {
  const pairs = new Map<string, PlayerOption>()
  for (const row of view.rows) {
    const key = keyOf(row.source_id, row.player)
    if (!pairs.has(key)) {
      pairs.set(key, { sourceId: row.source_id, player: row.player, playerLabel: labelOf(view.rows, row.source_id, row.player) })
    }
  }
  return [...pairs.values()].sort(byPlayer)
}

/** The voice-over groups of the view with the variants of each: one column per group, alphabetically, the variants
 *  by source and player. */
export function voiceColumns(view: EpisodeView): VoiceColumn[] {
  const found = new Map<string, { group: string; sourceId: number; player: string; heights: number[]; state: Variant["state"] }>()
  for (const row of view.rows) {
    const key = keyOf(row.voice_label, row.source_id, row.player)
    const variant = found.get(key)
      ?? { group: row.voice_label, sourceId: row.source_id, player: row.player, heights: [], state: "absent" as Variant["state"] }
    if (row.state === "playable") {
      variant.heights.push(...(row.heights ?? []))
      if (isSingle(row)) variant.state = "referable"
      else if (variant.state === "absent") variant.state = "playable"
    }
    found.set(key, variant)
  }
  const columns = new Map<string, VoiceColumn>()
  for (const { heights, ...pair } of found.values()) {
    const variant: Variant = {
      ...pair,
      playerLabel: labelOf(view.rows, pair.sourceId, pair.player),
      height: heights.length > 0 ? Math.max(...heights) : null,
    }
    const column = columns.get(variant.group)
    if (column) column.variants.push(variant)
    else columns.set(variant.group, { group: variant.group, variants: [variant] })
  }
  return [...columns.values()]
    .sort((a, b) => compareNames(a.group, b.group))
    .map((column) => ({ ...column, variants: column.variants.sort(byPlayer) }))
}

// --- the values of the levers ----------------------------------------------------------------------------------------

export interface LeverState {
  value: unknown
  level: Schemas["Level"]
}

/** The value of a lever for a target and the level it comes from; no record means the default the core gave the lever. */
export function leverState(
  levers: Lever[], specs: LeverSpec[], lever: string, target: string | null | undefined,
): LeverState {
  const wanted = target ?? null
  const found = levers.find((each) => each.lever === lever && (each.target ?? null) === wanted)
  if (found) return { value: found.value, level: found.level }
  return { value: specs.find((spec) => spec.name === lever)?.default ?? null, level: "default" }
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
  return { season: 0, episode: 0, references: [], rows, levers: [], dubs: [], availability: views[0]?.availability ?? {} }
}

// --- the lines of the output of an episode ---------------------------------------------------------------------------

/** One line of "the voice-overs on the output": the reference or a voice-over, the variant it comes from and its status.
 *  The status of a reference is the status of its file (null: no file of the plan), that of a voice-over its own state. */
export interface OutputLine {
  key: string
  kind: "reference" | "dub"
  group: string
  part: number | null
  /** The code and the label of the player, and the best height of the variant; null where the core names none. */
  player: string | null
  playerLabel: string | null
  height: number | null
  file: string | null
  dub: VoiceDub | null
}

/** The reference of each part of the episode first, then its voice-overs: each with the player it comes from and the best
 *  height of that variant, as the layout has it for this episode. A reference with no source names no variant, so it has
 *  no line: its mark says why. */
export function outputLines(view: EpisodeView): OutputLine[] {
  const variants = voiceColumns(view).flatMap((column) => column.variants)
  const labelOf = (sourceId: number | null, player: string | null): string | null =>
    player === null ? null : (view.labels?.[`${sourceId}:${player}`]
      ?? variants.find((each) => each.sourceId === sourceId && each.player === player)?.playerLabel ?? player)
  const lines: OutputLine[] = []
  for (const reference of view.references) {
    if ((reference.source_id ?? null) === null) continue
    const player = reference.player ?? null
    const file = (view.files ?? []).find((each) => each.source_id === reference.source_id && each.row_key === (reference.row_key ?? null))
    lines.push({
      key: `reference:${reference.part ?? 0}`, kind: "reference", group: reference.voice_group ?? "", part: reference.part ?? null,
      player, playerLabel: labelOf(reference.source_id ?? null, player), height: reference.height ?? null,
      file: file?.status ?? null, dub: null,
    })
  }
  for (const dub of view.dubs) {
    const sourceId = dub.source_id ?? null
    const player = dub.player ?? null
    const variant = variants.find((each) => each.group === dub.voice_label && each.sourceId === sourceId && each.player === player)
    lines.push({
      key: `dub:${dub.part ?? 0}:${dub.voice_key}`, kind: "dub", group: dub.voice_label, part: dub.part ?? null,
      player, playerLabel: labelOf(sourceId, player), height: variant?.height ?? null, file: null, dub,
    })
  }
  return lines
}
