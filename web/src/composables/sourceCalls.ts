import { api } from "../api/client"
import type { components } from "../api/schema"
import { t, type MessageKey } from "../i18n"
import { reasonText } from "../lib/reason"
import type { AppSearchReply, PreviewReply, ResolveReply, SeriesSource, SourceDraft } from "../lib/sources"

/** The answer of a call to the core: the data, or the words for the refusal (the reason worded by the catalog). */
export type Answer<T> = { ok: true; data: T } | { ok: false; text: string }

/** The catalog keys of the words a call gives when the core refuses it or cannot be reached; each window names its own. */
export interface CallWords {
  refused: MessageKey
  network: MessageKey
}

const SOURCE_WORDS: CallWords = { refused: "sources.error.refused", network: "sources.error.network" }

function refusal(error: unknown, words: CallWords): string {
  const body = error as { error?: unknown } | null | undefined
  const text = typeof body?.error === "string" ? reasonText(body.error) : ""
  return text || t(words.refused)
}

/** One call to the core. The words default to those of the sources; a window of another area passes its own. */
export async function call<T>(
  send: () => Promise<{ data?: unknown; error?: unknown }>,
  words: CallWords = SOURCE_WORDS,
): Promise<Answer<T>> {
  try {
    const { data, error } = await send()
    if (error || data === undefined) return { ok: false, text: refusal(error, words) }
    return { ok: true, data: data as T }
  } catch {
    return { ok: false, text: t(words.network) }
  }
}

/** Which site reads an address and its canonical form; the core answers without the network. */
export function resolveAddress(url: string): Promise<Answer<ResolveReply>> {
  return call(() => api.POST("/api/sources/resolve", { body: { url } }))
}

/** A page of the search of the video applications: the cards of each as it gives them, or the reason it gave none. */
export function searchApps(query: string, page = 1): Promise<Answer<AppSearchReply>> {
  return call(() => api.POST("/api/sources/search", { body: { query, page, app: "" } }))
}

/** What the window shows of an address: the state of its table, the summary and the lines. `refresh: false` reads nothing. */
export function previewAddress(body: {
  url: string
  profile_id?: number | null
  refresh?: boolean
  players_off?: string[]
  vibix_season?: number | null
}): Promise<Answer<PreviewReply>> {
  return call(() => api.POST("/api/sources/preview", { body: { refresh: true, ...body } }))
}

/** Reads the page now, whatever the age of its table; how the read goes and where it ends come as events. */
/** When the tables of the pages were read and until when they are current (the term of their site). */
export function fetchFreshness(urls: string[]): Promise<Answer<{ pages: components["schemas"]["TableReady"][] }>> {
  return call(() => api.POST("/api/sources/fresh", { body: { urls } }))
}

export function readPageNow(url: string, rows = "all"): Promise<Answer<unknown>> {
  return call(() => api.POST("/api/sources/read", { body: { url, rows } }))
}

/** Stops the read of the page going now: it ends failed, the last table serves; the end comes as an event. */
export function stopReading(url: string): Promise<Answer<unknown>> {
  return call(() => api.POST("/api/sources/cancel", { body: { url } }))
}

export async function fetchProfiles(): Promise<{ id: number; name: string }[]> {
  const { data } = await api.GET("/api/parser-profiles")
  return Array.isArray(data) ? (data as { id: number; name: string }[]) : []
}

export function fetchSources(seriesId: number): Promise<Answer<SeriesSource[]>> {
  return call(() => api.GET("/api/series/{series_id}/sources", { params: { path: { series_id: seriesId } } }))
}

export function createSource(seriesId: number, draft: SourceDraft): Promise<Answer<{ id: number }>> {
  return call(() => api.POST("/api/series/{series_id}/sources", { params: { path: { series_id: seriesId } }, body: draft }))
}

export function changeSource(seriesId: number, sourceId: number, fields: Record<string, unknown>): Promise<Answer<unknown>> {
  return call(() => api.PUT("/api/series/{series_id}/sources/{source_id}", {
    params: { path: { series_id: seriesId, source_id: sourceId } }, body: fields,
  }))
}

export function removeSource(seriesId: number, sourceId: number): Promise<Answer<unknown>> {
  return call(() => api.DELETE("/api/series/{series_id}/sources/{source_id}", {
    params: { path: { series_id: seriesId, source_id: sourceId } },
  }))
}
