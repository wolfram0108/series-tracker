import type { components } from "../api/schema"
import type { MediaFile } from "../composables/outputCalls"
import { NO_LIVE, dubKey, slotKey, type Live } from "./live"
import { spansOf } from "./ranges"

type Schemas = components["schemas"]
export type PlannerView = Schemas["PlannerView"]
export type PlanRowRef = Schemas["PlanRowRef"]
export type SeasonVoiceOrder = Schemas["SeasonVoiceOrder"]

/** The state of a voice-over of an episode as the strip of the episode shows it. */
export type DubState = "done" | "downloading" | "download_queued" | "align_queued" | "running" | "planned" | "error" | "absent" | "off"

/** A voice-over of an episode as the window reads it, told by the plan of the series: what its track is at. */
export interface VoiceDub {
  season: number
  episode: number
  part: number | null
  voice_key: string
  voice_label: string
  state: DubState
  source_id: number | null
  player: string | null
  /** Whether a row of a source gives it now; false: the asset holds it, the source forgot it. */
  online: boolean | null
  detail?: { code: string; params?: { [key: string]: string | number } } | null
}

/** A cut of an episode out of its compilation, told by the plan. */
export interface PlanCut {
  season: number
  episode: number
  part: number
  cut_key: string
  status: "planned" | "aligning" | "done" | "failed"
  from_ref_s?: number | null
  from_dub_s?: number | null
}

const sameRow = (a: PlanRowRef | null | undefined, b: PlanRowRef | null | undefined): boolean =>
  !!a && !!b && a.source_id === b.source_id && a.row_key === b.row_key && (a.video ?? "") === (b.video ?? "")

const partOf = (slot: number[]): number | null => (slot.length > 2 ? slot[2] : null)

/** The work of an episode that failed for the moment in the last sync (a crash of the service, a download cut short):
 *  an error of the episode until the next sync makes it; no failure, it is tried again. `voice` names the voice-over of
 *  an alignment or of a download of a voice-over, `video` the work on the video. */
function problemOf(view: PlannerView, slot: number[], match: { voice?: string; video?: boolean }) {
  const VIDEO = ["download_video", "cut", "download_picture"]
  return (view.problems ?? []).find((each) => each.slot != null && each.slot.join(":") === slot.join(":")
    && (match.video ? VIDEO.includes(each.kind) : !VIDEO.includes(each.kind) && each.voice === match.voice)) ?? null
}
const passing = (reason: string) => ({ code: "plan_passing", params: { reason } })

/** The slot of a row and the slot of an episode name the same video: the same episode and the same part, or no part. */
const sameSlot = (row: number[], episode: number[]): boolean =>
  row[0] === episode[0] && row[1] === episode[1] && partOf(row) === partOf(episode)

/** The order of the voice-overs of every season as the window shows it: the layout's whole order (the containers follow
 *  it), of which the voice-overs the rows give or the files of the season hold by the plan; a voice-over in the files the
 *  order does not know yet comes last. Each has the episodes that give it, online, else those whose files hold it, offline:
 *  no track of a file is left out of the window, and every one is in the place it takes in the containers. */
export interface ShownVoice {
  label: string
  episodes: string
  online: boolean
  /** The user's name of the track in the containers; none: the track bears the name of the group. */
  title?: string
  /** The voice-over of the markup reference: the last track of the files, out of the order the user drags (stage 14). */
  last?: boolean
}
export interface ShownOrder { season: number; voices: ShownVoice[] }

export function seasonVoiceOrders(orders: readonly SeasonVoiceOrder[], view: PlannerView | undefined,
                                  titles: Readonly<Record<string, string>> = {}): ShownOrder[] {
  const held = new Map<number, Map<string, number[]>>()
  // The sound of the markup reference is the last of the files of its season, out of its order (stage 14).
  const last = new Map<number, Set<string>>()
  for (const goal of view?.episodes ?? []) {
    const [season, episode] = goal.slot
    if (goal.markup) {
      const marked = last.get(season) ?? new Set<string>()
      last.set(season, marked.add(goal.markup))
    }
    const voices = held.get(season) ?? new Map<string, number[]>()
    held.set(season, voices)
    for (const voice of [goal.video.group ?? "", ...(goal.tracks ?? []).map((track) => track.voice)]) {
      if (!voice) continue
      const episodes = voices.get(voice)
      if (episodes) episodes.push(episode)
      else voices.set(voice, [episode])
    }
  }
  const seasons = [...new Set([...orders.map((each) => each.season), ...held.keys()])].sort((a, b) => a - b)
  return seasons.map((season) => {
    const known = orders.find((each) => each.season === season)?.voices ?? []
    const here = held.get(season) ?? new Map<string, number[]>()
    const named = new Set(known.map((voice) => voice.label))
    const shown = known.flatMap((voice) => voice.episodes ? [{ label: voice.label, episodes: voice.episodes, online: true }]
      : here.has(voice.label) ? [{ label: voice.label, episodes: spansOf(here.get(voice.label) ?? []), online: false }] : [])
    const more = [...here.entries()]
      .filter(([voice]) => !named.has(voice)).map(([label, episodes]) => ({ label, episodes: spansOf(episodes), online: false }))
    const marked = last.get(season) ?? new Set<string>()
    const ordered = [...shown, ...more]
    const voices = [...ordered.filter((voice) => !marked.has(voice.label)),
      ...ordered.filter((voice) => marked.has(voice.label)).map((voice) => ({ ...voice, last: true }))]
    return { season, voices: voices.map((voice) => titles[voice.label] ? { ...voice, title: titles[voice.label] } : voice) }
  }).filter((each) => each.voices.length > 0)
}

/** The file of an episode as the plan makes it: the name its goal has, by the slot of the episode. */
export interface PlanName {
  season: number
  episode: number
  part: number | null
  name: string
}

export function planNames(view: PlannerView | undefined): PlanName[] {
  return (view?.episodes ?? []).map((goal) => ({ season: goal.slot[0], episode: goal.slot[1], part: partOf(goal.slot), name: goal.name }))
}

function failed(view: PlannerView, kind: string, row: PlanRowRef): boolean {
  return (view.failures ?? []).some((each) => each.kind === kind && sameRow(each.row, row))
}

/** The key of the compilation of a row, as the layout names its compilations; a row it does not name keys itself. */
export type CompilationKey = (row: PlanRowRef) => string
const ownKey: CompilationKey = (row) => JSON.stringify(row)

/** The voice-overs of every episode: a track the file holds is done; one the plan gets is planned, or while the sync runs what
 *  its work is at (`live`: queued, downloading, aligning); one whose download or alignment failed is an error until the
 *  failures are reset. A voice-over of the order of the season that the plan has no way to get for the episode is absent: the
 *  episode is put together in part. */
export function planDubs(view: PlannerView | undefined, orders: Record<number, readonly string[]> = {}, live: Live = NO_LIVE): VoiceDub[] {
  if (!view) return []
  const onlineOf = new Map<string, boolean | null>()
  for (const asset of view.assets ?? []) {
    for (const track of asset.tracks ?? []) onlineOf.set(JSON.stringify(track.row), track.online ?? null)
  }
  const dubs: VoiceDub[] = []
  for (const goal of view.episodes ?? []) {
    const [season, episode] = goal.slot
    for (const track of goal.tracks ?? []) {
      let state: DubState
      const problem = track.kind === "reuse" || view.running ? null : problemOf(view, goal.slot, { voice: track.voice })
      if (track.kind === "reuse") state = "done"
      else if (failed(view, "download", track.row) || failed(view, "align", track.row) || problem) state = "error"
      // A track no work names yet is only planned, whatever the sync is doing meanwhile.
      else state = view.running ? live.dubs.get(dubKey(season, episode, track.voice)) ?? "planned" : "planned"
      dubs.push({
        season, episode, part: partOf(goal.slot), voice_key: track.voice, voice_label: track.voice, state,
        source_id: track.row.source_id, player: track.player || null,
        online: onlineOf.get(JSON.stringify(track.row)) ?? (track.kind === "download" || track.kind === "timeline" ? true : null),
        ...(problem ? { detail: passing(problem.reason) } : {}),
      })
    }
    const taken = new Set([...(goal.tracks ?? []).map((track) => track.voice), goal.video.group ?? ""])
    for (const voice of orders[season] ?? []) {
      if (taken.has(voice)) continue
      // Why the plan has no track of it: the user switched it off; its attempt failed and the plan passes over it (an error
      // of the episode); else no source gives it.
      const off = (goal.voices_off ?? []).includes(voice)
      const failure = off ? undefined
        : (view.failures ?? []).find((each) => each.voice === voice && (each.episodes ?? []).some((slot) => sameSlot(slot, goal.slot)))
      dubs.push({ season, episode, part: partOf(goal.slot), voice_key: voice, voice_label: voice,
                  state: off ? "off" : failure ? "error" : "absent",
                  source_id: failure ? failure.row.source_id : null, player: null, online: null })
    }
  }
  return dubs
}

/** The video of every episode as a file of the plan: ready when the file holds it, else what its work is at. */
export function planFiles(view: PlannerView | undefined, keyOf: CompilationKey = ownKey, live: Live = NO_LIVE): MediaFile[] {
  if (!view) return []
  return (view.episodes ?? []).map((goal) => {
    const row = goal.video.row
    let status = "pending"
    if (goal.video.kind === "asset") status = "completed"
    else if (failed(view, "download", row) || (!view.running && problemOf(view, goal.slot, { video: true }))) status = "error"
    else if (view.running) status = live.videos.get(slotKey(goal.slot[0], goal.slot[1])) ?? "pending"
    const held = (view.assets ?? []).find((asset) => asset.name === goal.video.file)
    const cut = held?.video.cut_of ? { key: keyOf(held.video.cut_of) } : null
    const pending = goal.video.kind === "cut" && goal.video.cut_of ? { key: keyOf(goal.video.cut_of) } : null
    return { unique_id: `${goal.slot.join(":")}`, source_id: row.source_id, row_key: row.row_key, status, cut,
             pending_cut: pending, online: goal.video.kind === "asset" ? held?.video.online ?? null : true }
  })
}

/** Where a video or a voice-over of a file comes from, as a variant of a queue is told: its source and its player, and for the
 *  video — a queue of single videos has a variant for each voice-over of one source and player — its voice-over. */
export interface Held { source_id: number; player: string; group?: string }

/** Whether a variant of a queue is the one `held` names: one source, one player, and, where the held one names it, one voice-over.
 *  The place every queue asks this: the mark «в файле», the reference the plan takes, the row of the file shown first. */
export const isVariantOf = (held: Held | null | undefined, row: { group: string; source_id: number; player: string }): boolean =>
  held != null && row.source_id === held.source_id && row.player === held.player && (held.group == null || row.group === held.group)

/** What the file of an episode holds now — the fact, as against what the order of a queue or the plan chooses: the source and
 *  the player of its video (of the picture, for a cut) and of each of its voice-overs by group, the own sound of the video
 *  among them. None for an episode with no file. */
export interface HeldFile {
  video: Held
  tracks: Record<string, Held>
  /** The variant of the queue of the markup reference whose timeline the video lies on; none the plan does not tell. */
  timeline: Held | null
}

export function heldFile(view: PlannerView | undefined, season: number, episode: number, part: number | null = null): HeldFile | null {
  const asset = (view?.assets ?? []).find((each) => !each.off && each.episode[0] === season && each.episode[1] === episode
    && partOf(each.episode) === part)
  if (!asset) return null
  const video: Held = { source_id: (asset.video.cut_of ?? asset.video.row).source_id, player: asset.video.player,
    ...(asset.video.label || asset.video.group ? { group: asset.video.label || asset.video.group! } : {}) }
  const tracks: Record<string, Held> = {}
  if (asset.video.group) tracks[asset.video.group] = video
  for (const track of asset.tracks ?? []) tracks[track.group ?? ""] = { source_id: track.row.source_id, player: track.player }
  const line = asset.timeline
  return { video, tracks, timeline: line ? { source_id: line.source_id, player: line.player, group: line.group } : null }
}

/** The variant the plan takes for the reference of an episode now: its source and its player (the single video whose
 *  timeline a cut is laid on, for a cut). None while the plan takes none: the queue is then read as the order says. */
export function takenReference(view: PlannerView | undefined, season: number, episode: number, part: number | null = null): Held | null {
  const found = (view?.references ?? []).find((each) => each.season === season && each.episode === episode && (each.part ?? null) === part)
  return found?.source_id != null && found.player
    ? { source_id: found.source_id, player: found.player, ...(found.voice_group ? { group: found.voice_group } : {}) } : null
}

/** The variant of the queue of the markup reference the plan takes for an episode; none while the episode follows its reference. */
export function takenMarkup(view: PlannerView | undefined, season: number, episode: number, part: number | null = null): Held | null {
  const goal = (view?.episodes ?? []).find((each) => each.slot[0] === season && each.slot[1] === episode && partOf(each.slot) === part)
  const variant = goal?.markup_variant
  return variant ? { source_id: variant.source_id, player: variant.player, group: variant.group } : null
}

/** What the plan takes for an episode: the variant of its reference and the one of its markup reference. */
export interface Taken { reference: Held | null; markup: Held | null }

export const takenOf = (view: PlannerView | undefined, season: number, episode: number, part: number | null = null): Taken =>
  ({ reference: takenReference(view, season, episode, part), markup: takenMarkup(view, season, episode, part) })

/** The cuts of the episodes out of their compilations: done when the file holds a cut video, planned or under way while
 *  the plan cuts it, failed when the cut failed. */
export function planCuts(view: PlannerView | undefined, keyOf: CompilationKey = ownKey): PlanCut[] {
  if (!view) return []
  const cuts: PlanCut[] = []
  for (const goal of view.episodes ?? []) {
    const [season, episode] = goal.slot
    const held = (view.assets ?? []).find((asset) => asset.name === goal.video.file)
    if (goal.video.kind === "cut") {
      const status = (view.failures ?? []).some((each) => each.kind === "cut" && sameRow(each.video, goal.video.row))
        ? "failed" : view.running ? "aligning" : "planned"
      cuts.push({ season, episode, part: partOf(goal.slot) ?? 0,
                  cut_key: goal.video.cut_of ? keyOf(goal.video.cut_of) : ownKey(goal.video.row), status })
    } else if (held?.video.cut_of) {
      cuts.push({ season, episode, part: partOf(goal.slot) ?? 0, cut_key: keyOf(held.video.cut_of), status: "done" })
    }
  }
  return cuts
}

/** The file of an episode and its track of a voice-over, as the command «Удалить» names them. */
export function fileOf(view: PlannerView | undefined, season: number, episode: number, part: number | null): string | null {
  const goal = (view?.episodes ?? []).find((each) => each.slot[0] === season && each.slot[1] === episode
    && partOf(each.slot) === (part ?? null))
  return goal?.video.file ?? null
}

/** The track of a voice-over a file holds, with its row and whether a source still gives it (`online`). */
export function trackOf(view: PlannerView | undefined, name: string, voice: string): { row: PlanRowRef; online?: boolean | null } | null {
  const asset = (view?.assets ?? []).find((each) => each.name === name)
  return asset?.tracks?.find((track) => track.group === voice || track.label === voice) ?? null
}
