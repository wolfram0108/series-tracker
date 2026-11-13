import type { ConformTask, DownloadCard, PackCard } from "../stores/queues"
import { dubRows, splitTitle, verdictOf } from "./conform"
import { sanitizeName } from "./fsnames"
import type { DubState } from "./plan"

/**
 * What the agents are doing for the episodes of a series now, as the window of «Выход» colours them: the work of the
 * downloads, of the alignment and of the packing, told by the cards that are of this series, by the episode and the voice-over
 * each is for. The plan says what is to be done; this says how far each piece of it is. A piece no card names is only planned.
 */
export type VideoLive = "queued" | "downloading" | "error"
/** The container of an episode: waiting for its turn, being built or put in place, or failed. */
export type PackLive = "queued" | "pack" | "failed"
export interface Live {
  /** By `season:episode:voice-over`: a download of its file, or its alignment, queued or going; or one that failed. */
  dubs: ReadonlyMap<string, DubState>
  /** By `season:episode`: the download of the video of the episode. */
  videos: ReadonlyMap<string, VideoLive>
  /** By `season:episode`: the container of the episode. */
  packs: ReadonlyMap<string, PackLive>
}

export const NO_LIVE: Live = { dubs: new Map(), videos: new Map(), packs: new Map() }

/** The voice-over of an episode by the name its file has in a job of the service: the planner names it by its voice, as a file
 *  may be named (core/fsnames.py), so the voice is written so here too. */
export const dubKey = (season: number, episode: number, voice: string): string => `${season}:${episode}:${sanitizeName(voice)}`
export const slotKey = (season: number, episode: number): string => `${season}:${episode}`

/** `s01e30` is the slot of one episode; the label of a compilation (`s01e09-e12`) names no one episode. */
function slotOf(label: string): { season: number; episode: number } | null {
  const found = /^s(\d+)e(\d+)$/i.exec(label.trim())
  return found ? { season: Number(found[1]), episode: Number(found[2]) } : null
}

const DOWNLOADING = new Set(["link", "download", "remux", "check"])

/** The work of the cards of the series, as plain data: a list of `[kind, key, state]` that equals itself while nothing but the
 *  figures of the work (the share, the speed) changed, so that a window recounts its episodes only when a state does. */
export type LiveEntry = ["dub" | "video" | "pack", string, string]

export function liveEntries(seriesId: number, downloads: readonly DownloadCard[], jobs: readonly ConformTask[],
                            packs: readonly PackCard[], nameOf: (jobId: string, file: string) => string): LiveEntry[] {
  const entries: LiveEntry[] = []
  for (const card of downloads) {
    if (card.need.series_id !== seriesId) continue
    const slot = slotOf(card.need.episode)
    if (!slot) continue
    const key = slotKey(slot.season, slot.episode)
    const state = card.status === "queued" ? "queued" : DOWNLOADING.has(card.status) ? "downloading" : card.status === "failed" ? "error" : null
    if (state === null) continue
    if (card.need.kind === "dub") {
      entries.push(["dub", dubKey(slot.season, slot.episode, card.need.group ?? ""), state === "queued" ? "download_queued" : state === "error" ? "error" : "downloading"])
    } else if (card.need.kind === "video") {
      entries.push(["video", key, state])
    }
  }
  for (const task of jobs) {
    if (task.series_id !== seriesId) continue
    const slot = slotOf(splitTitle(task.label).episode)
    if (!slot) continue
    for (const row of dubRows(task, (file) => nameOf(task.id, file))) {
      const key = dubKey(slot.season, slot.episode, row.name)
      if (row.result) {
        const verdict = verdictOf(row.result)
        if (verdict === "rejected" || verdict === "failed") entries.push(["dub", key, "error"])
      } else if (row.state === "running") entries.push(["dub", key, "running"])
      else if (row.state === "waiting") entries.push(["dub", key, "align_queued"])
    }
  }
  for (const card of packs) {
    if (card.series_id !== seriesId) continue
    const slot = slotOf(card.episode)
    if (!slot) continue
    const state = card.status === "queued" ? "queued" : card.status === "failed" ? "failed" : card.status === "done" ? null : "pack"
    if (state !== null) entries.push(["pack", slotKey(slot.season, slot.episode), state])
  }
  return entries.sort((a, b) => (a[0] + a[1] + a[2]).localeCompare(b[0] + b[1] + b[2]))
}

export function liveFrom(entries: readonly LiveEntry[]): Live {
  const dubs = new Map<string, DubState>(), videos = new Map<string, VideoLive>(), packs = new Map<string, PackLive>()
  for (const [kind, key, state] of entries) {
    if (kind === "dub") dubs.set(key, state as DubState)
    else if (kind === "video") videos.set(key, state as VideoLive)
    else packs.set(key, state as PackLive)
  }
  return { dubs, videos, packs }
}
