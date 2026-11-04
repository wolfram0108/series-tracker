import { defineStore } from "pinia"
import { ref } from "vue"
import type { components } from "../api/schema"

type Schemas = components["schemas"]

/** How a page stands for the screen, by the canonical address the core names it with. */
export type PageState = "reading" | "ready" | "failed"

export interface PageProgress {
  phase: string
  current: number
  total: number
  /** Steps of the phase per second since it began; null before the first step. */
  rate: number | null
}

export interface PageStatus {
  state: PageState
  progress: PageProgress | null
  /** Coded reason of the last failed read; worded by `reasonText`. */
  reason: string | null
  readAt: string | null
}

/** The reading of site pages as the core reports it (sources_table_progress / _ready / _failed), keyed by the
 *  canonical address of the page: the address the user typed is not the key. */
export const useSourceTablesStore = defineStore("sourceTables", () => {
  const pages = ref<Record<string, PageStatus>>({})

  function put(url: string, next: Partial<PageStatus> & { state: PageState }): void {
    const before = pages.value[url]
    pages.value = {
      ...pages.value,
      [url]: { progress: null, reason: null, readAt: before?.readAt ?? null, ...next },
    }
  }

  function applyProgress(packet: Schemas["TableProgress"]): void {
    put(packet.url, {
      state: "reading",
      progress: { phase: packet.phase, current: packet.current, total: packet.total, rate: packet.rate ?? null },
    })
  }

  function applyReady(packet: Schemas["TableReady"]): void {
    put(packet.url, { state: "ready", readAt: packet.read_at })
  }

  function applyFailed(packet: Schemas["TableFailed"]): void {
    put(packet.url, { state: "failed", reason: packet.reason })
  }

  function forUrl(url: string): PageStatus | null {
    return pages.value[url] ?? null
  }

  /** After a break of the connection the news of the pages may be incomplete: the screens ask their API again. */
  function reset(): void {
    pages.value = {}
  }

  return { pages, applyProgress, applyReady, applyFailed, forUrl, reset }
})
