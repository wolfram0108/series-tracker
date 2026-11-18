import { defineStore } from "pinia"
import type { components } from "../api/schema"
import { fetchFreshness } from "../composables/sourceCalls"
import { keyed } from "./keyed"

type Schemas = components["schemas"]

/** When the table of a page was read and until when it is current (the term of its site), by the canonical address. */
export interface Freshness {
  readAt: string
  freshUntil: string | null
}

/** The freshness of the tables of pages as the core tells it: asked once for the pages a window shows, then carried on by
 *  the events `sources_table_ready` (a new read) and `sources_table_fresh` (the term of the site moved). */
export const useFreshnessStore = defineStore("freshness", () => {
  const { all: pages, put, clear } = keyed<Freshness>()

  function apply(packet: Schemas["TableReady"]): void {
    put(packet.url, { readAt: packet.read_at, freshUntil: packet.fresh_until ?? null })
  }

  /** A freshness that cannot be read now shows no dot: the events of the next read bring it. */
  async function load(urls: string[]): Promise<void> {
    if (!urls.length) return
    const answer = await fetchFreshness(urls)
    if (answer?.ok) for (const page of answer.data.pages ?? []) apply(page)
  }

  function forUrl(url: string): Freshness | null {
    return pages[url] ?? null
  }

  return { pages, apply, load, forUrl, reset: clear }
})
