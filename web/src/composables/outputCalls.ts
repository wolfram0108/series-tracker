import { api } from "../api/client"
import type { components } from "../api/schema"
import type { Reference } from "../lib/output"
import { call, type Answer, type CallWords } from "./sourceCalls"

type Schemas = components["schemas"]

/** The layout of a video series: its rows, its sources and the levers with their settings. */
export type Composition = Schemas["CompositionReply"]

const OUTPUT_WORDS: CallWords = { refused: "output.error.refused", network: "output.error.network" }

export function fetchComposition(seriesId: number): Promise<Answer<Composition>> {
  return call(() => api.GET("/api/series/{series_id}/composition", { params: { path: { series_id: seriesId } } }), OUTPUT_WORDS)
}

/** The reference of every episode of the series, each with the mark that says why it is that one. */
export function fetchOutput(seriesId: number): Promise<Answer<Reference[]>> {
  return call(() => api.GET("/api/series/{series_id}/output", { params: { path: { series_id: seriesId } } }), OUTPUT_WORDS)
}

/** A pass over the series now; the core answers when the pass is over, and what it changed comes as events. */
export function syncSeries(seriesId: number): Promise<Answer<unknown>> {
  // The status of the files and of the plan is brought up to date; a download is the scan of the series card.
  return call(() => api.POST("/api/series/{series_id}/scan", {
    params: { path: { series_id: seriesId }, query: { download: false } },
  }), OUTPUT_WORDS)
}

/** The names the files of the series get by the rules as they are now, each with the slot it belongs to. */
export interface RenameRow {
  unique_id: string
  season?: number | null
  episode?: number | null
  part?: number | null
  new_filename_preview?: string | null
}

export function fetchRenames(seriesId: number): Promise<Answer<{ preview: RenameRow[] }>> {
  return call(() => api.GET("/api/series/{series_id}/rename_preview", { params: { path: { series_id: seriesId } } }), OUTPUT_WORDS)
}

/** The renaming of the files of the series is counted again by the rules as they are now. */
export function reprocessSeries(seriesId: number): Promise<Answer<unknown>> {
  return call(() => api.POST("/api/series/{series_id}/reprocess", { params: { path: { series_id: seriesId } } }), OUTPUT_WORDS)
}

/** The files already in the library are looked for and adopted into the plan. */
export function adoptSeries(seriesId: number): Promise<Answer<unknown>> {
  return call(() => api.POST("/api/series/{series_id}/deep-adoption", { params: { path: { series_id: seriesId } } }), OUTPUT_WORDS)
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

export function resetLever(seriesId: number, body: Schemas["OutputSettingsReset"]): Promise<Answer<unknown>> {
  return call(() => api.POST("/api/series/{series_id}/output/settings/reset", {
    params: { path: { series_id: seriesId } }, body,
  }), OUTPUT_WORDS)
}
