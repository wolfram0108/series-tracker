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

/** The file of a row of the plan as the downloads keep it: the status of the video of a reference is read from it. */
export interface MediaFile {
  unique_id: string
  source_id: number | null
  row_key: string | null
  status: string
}

/** The files of the plan of the series (the rows of its plan with their status). */
export async function fetchMedia(seriesId: number): Promise<Answer<MediaFile[]>> {
  const answer = await call<unknown>(
    () => api.GET("/api/series/{series_id}/media-items", { params: { path: { series_id: seriesId } } } as never),
    OUTPUT_WORDS,
  )
  if (!answer.ok) return answer
  const items = Array.isArray(answer.data) ? (answer.data as Partial<MediaFile>[]) : []
  return {
    ok: true,
    data: items.map((item) => ({
      unique_id: String(item.unique_id ?? ""), source_id: item.source_id ?? null, row_key: item.row_key ?? null, status: String(item.status ?? ""),
    })),
  }
}

/** The reference of every episode of the series, each with the mark that says why it is that one. */
export function fetchOutput(seriesId: number): Promise<Answer<Reference[]>> {
  return call(() => api.GET("/api/series/{series_id}/output", { params: { path: { series_id: seriesId } } }), OUTPUT_WORDS)
}

/** The sync of the series, the same run the series card starts: tables, plan, download, voice-overs, containers. The core
 * answers when its pass is over; the run goes on with the works it gave out, and what it changes comes as events. */
export function syncSeries(seriesId: number): Promise<Answer<unknown>> {
  return call(() => api.POST("/api/series/{series_id}/scan", { params: { path: { series_id: seriesId } } }), OUTPUT_WORDS)
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

export function setVoiceOrder(seriesId: number, body: Schemas["VoiceOrderBody"]): Promise<Answer<unknown>> {
  return call(() => api.PUT("/api/series/{series_id}/output/voice-order", {
    params: { path: { series_id: seriesId } }, body,
  }), OUTPUT_WORDS)
}

export function resetLever(seriesId: number, body: Schemas["OutputSettingsReset"]): Promise<Answer<unknown>> {
  return call(() => api.POST("/api/series/{series_id}/output/settings/reset", {
    params: { path: { series_id: seriesId } }, body,
  }), OUTPUT_WORDS)
}
