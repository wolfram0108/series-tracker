import { defineStore } from "pinia"
import { ref } from "vue"
import type { components } from "../api/schema"

type Schemas = components["schemas"]

export type LayoutChanged = Schemas["CompositionChanged"]
export type LayoutFailed = Schemas["CompositionFailed"]
export type ReferencePacket = Schemas["ReferenceChanged"]

const slot = (season: number, episode: number) => `${season}:${episode}`

/** The newest packets of the layout and of the references, per series. A packet is kept as the core sent it:
 *  a screen that shows the output reads from here or asks the API again when `layoutChanged` moves. */
export const useCompositionStore = defineStore("composition", () => {
  // composition_changed: the one with the greater `rev` wins, so a late packet cannot roll the screen back.
  const layoutChanged = ref<Record<number, LayoutChanged>>({})
  // composition_failed: the previous layout stays in force; the reason holds until the next change arrives.
  const layoutFailed = ref<Record<number, LayoutFailed>>({})
  // reference_changed: the whole reference of one episode, so the last packet per (season, episode) is the truth.
  const references = ref<Record<number, Record<string, ReferencePacket>>>({})
  // series_sources_changed: only the fact that the sources of a series changed moves a counter; a screen asks again.
  const sourcesChanged = ref<Record<number, number>>({})

  function applyChanged(packet: LayoutChanged): void {
    const known = layoutChanged.value[packet.series_id]
    if (known && known.rev >= packet.rev) return
    layoutChanged.value = { ...layoutChanged.value, [packet.series_id]: packet }
    if (packet.series_id in layoutFailed.value) {
      const rest = { ...layoutFailed.value }
      delete rest[packet.series_id]
      layoutFailed.value = rest
    }
  }

  function applyFailed(packet: LayoutFailed): void {
    layoutFailed.value = { ...layoutFailed.value, [packet.series_id]: packet }
  }

  function applyReference(packet: ReferencePacket): void {
    const own = references.value[packet.series_id] ?? {}
    references.value = {
      ...references.value,
      [packet.series_id]: { ...own, [slot(packet.season, packet.episode)]: packet },
    }
  }

  function applySourcesChanged(packet: Schemas["SourceChanged"]): void {
    sourcesChanged.value = { ...sourcesChanged.value, [packet.series_id]: (sourcesChanged.value[packet.series_id] ?? 0) + 1 }
  }

  function referenceOf(seriesId: number, season: number, episode: number): ReferencePacket | null {
    return references.value[seriesId]?.[slot(season, episode)] ?? null
  }

  return {
    layoutChanged, layoutFailed, references, sourcesChanged,
    applyChanged, applyFailed, applyReference, applySourcesChanged, referenceOf,
  }
})
