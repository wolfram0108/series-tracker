import { defineStore } from "pinia"
import { ref } from "vue"
import type { components } from "../api/schema"
import type { PageProgress } from "./sourceTables"

type Schemas = components["schemas"]

/** The read of a page that a scan of a series waits for, per source. */
export interface SeriesRead extends PageProgress {
  sourceId: number
  url: string
}

/** The reads the scans wait for, as the core reports them (series_scan_progress), by series and source. A packet with
 *  `active` false ends the read of its source; nothing is cleared on a clock. */
export const useSeriesReadsStore = defineStore("seriesReads", () => {
  const reads = ref<Record<number, Record<number, SeriesRead>>>({})

  // The core sends every field; a packet without the ones that have defaults is read with them.
  type Packet = Pick<Schemas["SeriesReadProgress"], "series_id" | "source_id" | "url"> & Partial<Schemas["SeriesReadProgress"]>

  function apply(packet: Packet): void {
    const mine = { ...(reads.value[packet.series_id] ?? {}) }
    if (packet.active === false) delete mine[packet.source_id]
    else
      mine[packet.source_id] = {
        sourceId: packet.source_id, url: packet.url, phase: packet.phase ?? "", current: packet.current ?? 0,
        total: packet.total ?? 0, rate: packet.rate ?? null,
      }
    const next = { ...reads.value }
    if (Object.keys(mine).length) next[packet.series_id] = mine
    else delete next[packet.series_id]
    reads.value = next
  }

  function forSeries(seriesId: number): SeriesRead[] {
    return Object.values(reads.value[seriesId] ?? {})
  }

  /** The read furthest from its end: what the card of the series shows while the scan waits. */
  function lead(seriesId: number): SeriesRead | null {
    const all = forSeries(seriesId)
    return all.reduce<SeriesRead | null>(
      (best, each) => (best === null || each.total - each.current > best.total - best.current ? each : best), null)
  }

  /** A connection that came back may have missed the end of a read: the reads start again from the next packet. */
  function reset(): void {
    reads.value = {}
  }

  return { reads, apply, forSeries, lead, reset }
})
