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
