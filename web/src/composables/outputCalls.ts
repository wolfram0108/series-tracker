import { api } from "../api/client"
import type { components } from "../api/schema"
import { call, type Answer, type CallWords } from "./sourceCalls"
import type { LayoutPart } from "../lib/layoutParts"

type Schemas = components["schemas"]

/** The layout of a video series: its rows, its sources and the levers with their settings. */
export type Composition = Schemas["CompositionReply"]

const OUTPUT_WORDS: CallWords = { refused: "output.error.refused", network: "output.error.network" }

/** The layout of a series, or the part of it a change touched (`part`; `episodes` — `season:episode` of the part `levers`). */
export function fetchComposition(seriesId: number, part: LayoutPart = "whole", episodes: string[] = []): Promise<Answer<Composition>> {
  const path = { series_id: seriesId }
  return call(() => api.GET("/api/series/{series_id}/composition", {
    params: part === "whole" ? { path } : { path, query: { part, episodes } },
  }), OUTPUT_WORDS)
}

/** What the video of a container is cut of (stage 12): the key of its compilation and the seconds taken from it and from
 *  the episode itself. */
export interface CutOf { key: string; from_ref_s?: number | null; from_dub_s?: number | null }

/** The video of an episode as a file of its plan: the status of the video of a reference is read from it. */
export interface MediaFile {
  unique_id: string
  source_id: number | null
  row_key: string | null
  status: string
  /** The video of the container in place, and the one the next build puts in, when cut out of a compilation. */
  cut?: CutOf | null
  pending_cut?: CutOf | null
  /** Whether a source gives the row of the video now; false: the file keeps a video its source forgot. */
  online?: boolean | null
}


/** The sync of the series — or, with a scope, of a season or an episode of it: the same run the series card starts (the tables of
 * every source are read, the layout is counted), and the core carries out what the scope touches. The core
 * answers when its pass is over; the run goes on with the works it gave out, and what it changes comes as events. */
export function syncSeries(seriesId: number, scope?: Schemas["SyncScopeBody"]): Promise<Answer<unknown>> {
  const path = { params: { path: { series_id: seriesId } } }
  return call(() => api.POST("/api/series/{series_id}/scan", scope === undefined ? path : { ...path, body: scope }), OUTPUT_WORDS)
}

/** The sync of the series stops: its reads, downloads, jobs of the alignment service and builds; nothing of it is applied.
 * The core answers once every work of it has stopped; `cancelled` false when no sync went. */
export function cancelSync(seriesId: number): Promise<Answer<{ cancelled?: boolean }>> {
  return call(() => api.POST("/api/series/{series_id}/scan/cancel", { params: { path: { series_id: seriesId } } }),
    OUTPUT_WORDS)
}

/** The level whose failed attempts are forgotten: a season, an episode of it, a voice-over group, the cuts out of
 *  compilations. */
export type FailuresLevel = Schemas["FailuresResetBody"]

/** The failed attempts of the series are forgotten, or only those of one level of it (the others stay, and what the plan
 * chose because they failed stays chosen): a change of the series, not a sync. The voice-overs set back are fetched by the
 * next sync; what changes comes as events. */
export function resetFailures(seriesId: number, level?: FailuresLevel): Promise<Answer<unknown>> {
  const path = { params: { path: { series_id: seriesId } } }
  return call(() => api.POST("/api/series/{series_id}/failures/reset", level === undefined ? path : { ...path, body: level }),
    OUTPUT_WORDS)
}


/** Switches an episode (without `episode`, a whole season) off or on; the references that follow come as events. */
export function switchOutput(seriesId: number, body: Schemas["OutputSwitchBody"]): Promise<Answer<unknown>> {
  return call(() => api.PUT("/api/series/{series_id}/output/switch", {
    params: { path: { series_id: seriesId } }, body,
  }), OUTPUT_WORDS)
}

export function setLever(seriesId: number, body: Schemas["OutputSettingsSet"]): Promise<Answer<unknown>> {
  return call(() => api.PUT("/api/series/{series_id}/output/settings", {
    params: { path: { series_id: seriesId } }, body,
  }), OUTPUT_WORDS)
}

/** Several levers in one command: one write, one count of the plan for all of them; a refused one refuses them all. */
export function setLevers(seriesId: number, items: Schemas["OutputSettingsSet"][]): Promise<Answer<unknown>> {
  return call(() => api.PUT("/api/series/{series_id}/output/settings/batch", {
    params: { path: { series_id: seriesId } }, body: { items },
  }), OUTPUT_WORDS)
}

export function setVoiceOrder(seriesId: number, body: Schemas["VoiceOrderBody"]): Promise<Answer<unknown>> {
  return call(() => api.PUT("/api/series/{series_id}/output/voice-order", {
    params: { path: { series_id: seriesId } }, body,
  }), OUTPUT_WORDS)
}

export function setVoiceTitle(seriesId: number, body: Schemas["VoiceTitleBody"]): Promise<Answer<unknown>> {
  return call(() => api.PUT("/api/series/{series_id}/output/voice-title", {
    params: { path: { series_id: seriesId } }, body,
  }), OUTPUT_WORDS)
}

export function resetLever(seriesId: number, body: Schemas["OutputSettingsReset"]): Promise<Answer<unknown>> {
  return call(() => api.POST("/api/series/{series_id}/output/settings/reset", {
    params: { path: { series_id: seriesId } }, body,
  }), OUTPUT_WORDS)
}

/** The queues of a level (the series, a season, or an episode of it) in the order the core tries their variants. */
export type Queues = Schemas["QueuesReply"]

export function fetchQueues(seriesId: number, season: number | null, episode: number | null,
                            compilation: string | null = null): Promise<Answer<Queues>> {
  const query = { ...(season === null ? {} : { season }), ...(episode === null ? {} : { episode }),
    ...(compilation === null ? {} : { compilation }) }
  return call(() => api.GET("/api/series/{series_id}/output/queues", {
    params: { path: { series_id: seriesId }, query },
  }), OUTPUT_WORDS)
}

/** The plan of a video series: the asset, the goal of every episode, the failures and the commands that wait. */
export function fetchPlan(seriesId: number): Promise<Answer<Schemas["PlannerView"]>> {
  return call(() => api.GET("/api/series/{series_id}/plan", { params: { path: { series_id: seriesId } } }), OUTPUT_WORDS)
}

/** «Удалить» of a file or of a track in it: the next sync carries it out (a video goes only for another video). */
export function deleteFromPlan(seriesId: number, body: Schemas["PlanDeleteBody"]): Promise<Answer<unknown>> {
  return call(() => api.POST("/api/series/{series_id}/plan/delete", {
    params: { path: { series_id: seriesId } }, body,
  }), OUTPUT_WORDS)
}

/** The command «Удалить» that waits is taken back. */
export function cancelDelete(seriesId: number, body: Schemas["PlanDeleteBody"]): Promise<Answer<unknown>> {
  return call(() => api.POST("/api/series/{series_id}/plan/delete/cancel", {
    params: { path: { series_id: seriesId } }, body,
  }), OUTPUT_WORDS)
}
