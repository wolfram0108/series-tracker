import { defineStore } from "pinia"
import { keyed } from "./keyed"
import { joined, noted, type LayoutNews } from "../lib/layoutParts"
import type { components } from "../api/schema"

type Schemas = components["schemas"]

export type LayoutChanged = Schemas["CompositionChanged"]
export type LayoutFailed = Schemas["CompositionFailed"]
/** The newest packets of the layout, per series. A packet is kept as the core sent it: a screen that shows the output
 *  asks the API again when `layoutChanged` moves (the references are the plan's: stores/plan.ts). */
export const useCompositionStore = defineStore("composition", () => {
  // composition_changed: the one with the greater `rev` wins, so a late packet cannot roll the screen back.
  const { all: layoutChanged, put: putChanged } = keyed<LayoutChanged>()
  // composition_failed: the previous layout stays in force; the reason holds until the next change arrives.
  const { all: layoutFailed, put: putFailed, drop: dropFailed } = keyed<LayoutFailed>()
  // series_sources_changed: only the fact that the sources of a series changed moves a counter; a screen asks again.
  const { all: sourcesChanged, put: putSources } = keyed<number>()
  // What changed since the layout of each series was last read: the read that comes after takes it all at once.
  const news = new Map<number, LayoutNews>()

  function applyChanged(packet: LayoutChanged): void {
    const known = layoutChanged[packet.series_id]
    if (known && known.rev >= packet.rev) return
    news.set(packet.series_id, noted(news.get(packet.series_id), packet))
    putChanged(packet.series_id, packet)
    dropFailed(packet.series_id)
  }

  /** The news of the layout of a series since it was last read; taken by the read that begins now. */
  function takeNews(seriesId: number): LayoutNews | undefined {
    const held = news.get(seriesId)
    news.delete(seriesId)
    return held
  }

  /** A read that failed gives back the news it took: the next read takes them with its own. */
  function giveBack(seriesId: number, taken: LayoutNews): void {
    news.set(seriesId, joined(news.get(seriesId), taken))
  }

  function applyFailed(packet: LayoutFailed): void {
    putFailed(packet.series_id, packet)
  }

  function applySourcesChanged(packet: Schemas["SourceChanged"]): void {
    putSources(packet.series_id, (sourcesChanged[packet.series_id] ?? 0) + 1)
  }

  return {
    layoutChanged, layoutFailed, sourcesChanged,
    applyChanged, applyFailed, applySourcesChanged, takeNews, giveBack,
  }
})
