// The torrent editor of a source (stage 15, step 9): the shapes the core gives (modules/unpack, modules/torrents) and the
// pure functions that lay them out as the window shows them. Nothing here asks the core.

import { spansOf } from "./ranges"

export type ReleaseState = "metadata" | "downloading" | "ready" | "updating" | "failed" | "archived"
export type EditorTab = "files" | "voices" | "subtitles" | "streams"
export const EDITOR_TABS: readonly EditorTab[] = ["files", "voices", "subtitles", "streams"]

export interface ReleaseFile { path: string; size: number; progress: number; priority: number }
/** A release the core keeps for a page (torrents.releases.get). */
export interface ReleaseView {
  url: string
  release: string
  title: string
  published?: string | null
  infohash?: string | null
  state: ReleaseState
  reason?: string | null
  watched: boolean
  folder?: string | null
  pending: boolean
  files: ReleaseFile[]
}
/** A release as the page lists it now (sources.releases.get). */
export interface PageRelease { release: string; title: string; link?: string | null; magnet?: string | null; published?: string | null; episodes?: string | null }
export interface PageReleases { url: string; site: string; title: string; releases: PageRelease[] }

export interface Place { season: number; episode: number; last: number | null }
export interface StreamFacts {
  index: number
  kind: string
  codec?: string | null
  language?: string | null
  title?: string | null
  channels?: number | null
  height?: number | null
  forced?: boolean | null
  default?: boolean | null
}
export type FileKind = "video" | "audio" | "subtitles" | "font" | "other"
export interface FileEntry {
  item: string
  path: string
  line: string
  kind: FileKind
  size: number
  priority: number
  excluded: boolean
  place: Place | null
  by: string | null
  problem?: string | null
}
export interface TrackEntry {
  item: string
  line: string
  kind: "audio" | "subtitles"
  folder: string | null
  by: string | null
  excluded: boolean
  /** No folder or no known language yet (none, `und`): the track waits for a rule or a hand. */
  waiting: boolean
  language: string
  subtitle_type: string
  path: string
  place: Place | null
  stream: StreamFacts | null
}
export interface Folder {
  kind: "voice" | "subtitles"
  name: string
  items: number
  manual: boolean
  language: string
  subtitle_type: string
  /** The fonts the external files of a folder of subtitles go with (core/contracts/torrent.py fonts_of). */
  fonts: string[]
}
export interface ContainerFile { item: string; path: string; place: Place | null; streams: StreamFacts[] }
export interface EditorRelease { release: string; title: string; state: ReleaseState; reason?: string | null; pending: boolean
  reading: { done: number; total: number } | null }
export interface EditorView {
  releases: EditorRelease[]
  tabs: { files: FileEntry[]; voices: TrackEntry[]; subtitles: TrackEntry[]; streams: TrackEntry[] }
  folders: Folder[]
  unplaced: Record<EditorTab, number>
  containers: ContainerFile[]
}
export interface Editor { url: string; profiles: Record<EditorTab, number | null>; view: EditorView }

// --- the state of a source, as the head of the tab shows it ---------------------------------------------------------

export type SourceStage = "notLoaded" | "download" | "read" | "ready" | "update" | "failed" | "archived"
export interface SourceState {
  stage: SourceStage
  /** Files downloaded or read of how many (a download, an update, a read), and the share done: of the bytes of the wanted
   *  files for a download, of the files for a read. */
  progress: { done: number; total: number; share: number } | null
  /** Items no rule and no hand placed yet (a ready release). */
  waiting: number
  reason: string | null
}

/** The stage of a source by its release, the read of its files and what waits for the user (the board «Состояния»). */
export function sourceState(kept: ReleaseView | undefined, reading: { done: number; total: number } | null, waiting: number): SourceState {
  if (!kept) return { stage: "notLoaded", progress: null, waiting: 0, reason: null }
  const wanted = kept.files.filter((file) => file.priority > 0)
  const bytes = wanted.reduce((sum, file) => sum + file.size, 0)
  const got = wanted.reduce((sum, file) => sum + file.size * file.progress, 0)
  const fetched = { done: wanted.filter((file) => file.progress >= 1).length, total: wanted.length,
    share: bytes ? Math.floor((got / bytes) * 100) : 0 }
  if (kept.state === "metadata" || kept.state === "downloading") return { stage: "download", progress: fetched, waiting: 0, reason: null }
  if (kept.state === "updating") return { stage: "update", progress: fetched, waiting: 0, reason: null }
  if (kept.state === "failed") return { stage: "failed", progress: null, waiting: 0, reason: kept.reason ?? null }
  if (kept.state === "archived") return { stage: "archived", progress: null, waiting: 0, reason: null }
  if (reading && reading.done < reading.total) {
    return { stage: "read", progress: { ...reading, share: Math.floor((reading.done / reading.total) * 100) }, waiting: 0, reason: null }
  }
  return { stage: "ready", progress: null, waiting, reason: null }
}

// --- the rules that placed an item ----------------------------------------------------------------------------------

/** The rule an item was placed by, as the window words it: a row of a table is told by its number. The engine names the
 *  rule of a row of a table «name · N» (modules/rules/engine.table_rules). */
export function ruleOf(by: string | null): { rule: string; row: number | null } | null {
  if (!by || by === "manual" || by === FROM_META) return null
  const found = /^(.*) · (\d+)$/.exec(by)
  return found ? { rule: found[1], row: Number(found[2]) } : { rule: by, row: null }
}

// --- the tab «Файлы» ------------------------------------------------------------------------------------------------

/** How a track got its folder when no rule and no hand placed it: its title in the file (modules/unpack/layout.py). */
export const FROM_META = "meta"

export type FileFilter = "all" | "unplaced" | "placed" | "excluded" | "notInvolved"
const INVOLVED: readonly FileKind[] = ["video", "audio", "subtitles"]

export function fileFilterOf(entry: FileEntry): Exclude<FileFilter, "all"> {
  if (!INVOLVED.includes(entry.kind)) return "notInvolved"
  if (entry.excluded) return "excluded"
  return entry.place ? "placed" : "unplaced"
}

export function fileCounts(entries: readonly FileEntry[]): Record<FileFilter, number> {
  const counts: Record<FileFilter, number> = { all: entries.length, unplaced: 0, placed: 0, excluded: 0, notInvolved: 0 }
  for (const entry of entries) counts[fileFilterOf(entry)]++
  return counts
}

export const folderOf = (path: string): string => path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : ""
export const nameOf = (path: string): string => path.slice(path.lastIndexOf("/") + 1)

/** Items grouped by their folder in the release, the folders in the order of their first item. */
export function byFolder<T extends { path: string }>(entries: readonly T[]): { folder: string; entries: T[] }[] {
  const groups = new Map<string, T[]>()
  for (const entry of entries) {
    const key = folderOf(entry.path)
    const list = groups.get(key)
    if (list) list.push(entry)
    else groups.set(key, [entry])
  }
  return [...groups].map(([folder, list]) => ({ folder, entries: list }))
}

/** The folder of an episode in the draft, as the files of the library name it: `s01e05`, `s01e05-e06` for several. */
export function episodeFolder(season: number, episode: number, last: number | null = null): string {
  const two = (n: number): string => String(n).padStart(2, "0")
  const word = `s${two(season)}e${two(episode)}`
  return last !== null && last !== episode ? `${word}-e${two(last)}` : word
}

export interface DraftEpisode { episode: number; last: number | null; files: FileEntry[]; kinds: Partial<Record<FileKind, number>> }
export interface DraftSeason { season: number; episodes: DraftEpisode[]; manual: boolean }

/** The draft of the release: seasons, their episodes and the files laid in each, by the rules or by hand. */
export function draftOf(entries: readonly FileEntry[]): DraftSeason[] {
  const seasons = new Map<number, Map<string, DraftEpisode>>()
  const manual = new Set<number>()
  for (const entry of entries) {
    if (!entry.place || entry.excluded) continue
    const { season, episode, last } = entry.place
    const episodes = seasons.get(season) ?? new Map<string, DraftEpisode>()
    seasons.set(season, episodes)
    const key = `${episode}:${last ?? ""}`
    const found = episodes.get(key) ?? { episode, last, files: [], kinds: {} }
    episodes.set(key, found)
    found.files.push(entry)
    found.kinds[entry.kind] = (found.kinds[entry.kind] ?? 0) + 1
    if (entry.by === "manual") manual.add(season)
  }
  return [...seasons].sort((a, b) => a[0] - b[0]).map(([season, episodes]) => ({
    season, manual: manual.has(season),
    episodes: [...episodes.values()].sort((a, b) => a.episode - b.episode),
  }))
}

// --- the tabs of the tracks -----------------------------------------------------------------------------------------

export type TrackFilter = "all" | "unplaced" | "placed"
export function trackCounts(entries: readonly TrackEntry[]): Record<TrackFilter, number> {
  return { all: entries.length, unplaced: entries.filter((entry) => entry.waiting).length,
    placed: entries.filter((entry) => !entry.waiting && !entry.excluded).length }
}
export const trackFits = (entry: TrackEntry, filter: TrackFilter): boolean =>
  filter === "all" || (filter === "placed" ? !entry.waiting && !entry.excluded : entry.waiting)

/** Tracks grouped by the episode of their file; those of no episode last. */
export function byEpisode(entries: readonly TrackEntry[]): { place: Place | null; entries: TrackEntry[] }[] {
  const groups = new Map<string, { place: Place | null; entries: TrackEntry[] }>()
  for (const entry of entries) {
    const key = entry.place ? `${entry.place.season}:${entry.place.episode}:${entry.place.last ?? ""}` : ""
    const group = groups.get(key) ?? { place: entry.place, entries: [] }
    groups.set(key, group)
    group.entries.push(entry)
  }
  return [...groups.values()].sort((a, b) => !a.place ? 1 : !b.place ? -1
    : a.place.season - b.place.season || a.place.episode - b.place.episode)
}

export interface FolderView extends Folder {
  /** The seasons and episodes its items are of: `1: 1–146`. */
  episodes: string
  /** The rule that names it, when one does; none when every item of it was placed by hand or by its title in the file. */
  rule: { rule: string; row: number | null } | null
  /** Made by itself of the titles the files give (no rule, no hand). */
  fromMeta: boolean
  /** Its items among those given (the items of one tab). */
  held: number
}

/** The folders of a kind with what their items say of them (episodes, the rule that made them). */
export function foldersOf(folders: readonly Folder[], entries: readonly TrackEntry[], kind: Folder["kind"]): FolderView[] {
  return folders.filter((folder) => folder.kind === kind).map((folder) => {
    const mine = entries.filter((entry) => entry.folder === folder.name && !entry.excluded)
    const bySeason = new Map<number, number[]>()
    for (const entry of mine) {
      if (!entry.place) continue
      const list = bySeason.get(entry.place.season) ?? []
      list.push(entry.place.episode)
      bySeason.set(entry.place.season, list)
    }
    const episodes = [...bySeason].sort((a, b) => a[0] - b[0]).map(([season, numbers]) => `${season}: ${spansOf(numbers)}`).join(" · ")
    const rule = mine.map((entry) => ruleOf(entry.by)).find((each) => each !== null) ?? null
    return { ...folder, episodes, rule, held: mine.length, fromMeta: mine.length > 0 && mine.every((entry) => entry.by === FROM_META) }
  })
}

// --- the tab «Контейнеры» -------------------------------------------------------------------------------------------

export interface ContainerStream { facts: StreamFacts; label: string; items: string[]; folder: string | null; unplaced: boolean }
export interface ContainerGroup { key: string; season: number | null; episodes: string; files: number; streams: ContainerStream[] }

/** The letter and number of a stream as the editor names it: v0, a0, s0. */
export const streamLabel = (facts: StreamFacts, nth: number): string => `${facts.kind === "video" ? "v" : facts.kind === "audio" ? "a" : "s"}${nth}`

/** The video files of the release, those of one composition (kinds, codecs, languages, titles of their streams) and one
 *  season as one container; each stream of it with the items of every file and the folder they lie in. */
export function containerGroups(files: readonly ContainerFile[], entries: readonly TrackEntry[]): ContainerGroup[] {
  const byItem = new Map(entries.map((entry) => [entry.item, entry] as const))
  const groups = new Map<string, { files: ContainerFile[]; season: number | null }>()
  for (const file of files) {
    const signature = JSON.stringify([file.place?.season ?? null,
      file.streams.map((s) => [s.kind, s.codec ?? "", s.language ?? "", s.title ?? ""])])
    const group = groups.get(signature) ?? { files: [], season: file.place?.season ?? null }
    groups.set(signature, group)
    group.files.push(file)
  }
  return [...groups].map(([key, group]) => {
    const first = group.files[0]
    const nth: Record<string, number> = {}
    const streams = first.streams.map((facts, position) => {
      const n = nth[facts.kind] ?? 0
      nth[facts.kind] = n + 1
      const items = group.files.map((file) => `${file.item}#${file.streams[position]?.index ?? facts.index}`)
      const folders = new Set(items.map((item) => byItem.get(item)?.folder ?? null))
      const folder = folders.size === 1 ? [...folders][0] : null
      const unplaced = facts.kind !== "video" && items.some((item) => !!byItem.get(item)?.waiting)
      return { facts, label: streamLabel(facts, n), items: facts.kind === "video" ? [] : items, folder, unplaced }
    })
    return { key, season: group.season, files: group.files.length, streams,
      episodes: spansOf(group.files.flatMap((file) => file.place ? [file.place.episode] : [])) }
  }).sort((a, b) => (b.season ?? -1) - (a.season ?? -1) || a.episodes.localeCompare(b.episodes))
}
