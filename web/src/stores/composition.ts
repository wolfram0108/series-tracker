import { defineStore } from "pinia"
import { keyed } from "./keyed"
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

  function applyChanged(packet: LayoutChanged): void {
    const known = layoutChanged[packet.series_id]
    if (known && known.rev >= packet.rev) return
    putChanged(packet.series_id, packet)
    dropFailed(packet.series_id)
  }

  function applyFailed(packet: LayoutFailed): void {
    putFailed(packet.series_id, packet)
  }

  function applySourcesChanged(packet: Schemas["SourceChanged"]): void {
    putSources(packet.series_id, (sourcesChanged[packet.series_id] ?? 0) + 1)
  }

  return {
    layoutChanged, layoutFailed, sourcesChanged,
    applyChanged, applyFailed, applySourcesChanged,
  }
})
