import { defineStore } from "pinia"
import { ref } from "vue"

/** One additional voice-over of an episode: its state and the reason of a failure or rejection. */
export interface VoiceDub {
  season: number
  episode: number
  voice_key: string
  voice_label: string
  status: string
  detail: string | null
}

/** Packet of voice_dub_update: the whole voice-over, so nothing is asked back. */
export interface VoiceDubEvent extends VoiceDub {
  series_id: number
}

const slot = (seriesId: number, season: number, episode: number) => `${seriesId}:${season}:${episode}`

/** Voice-overs per episode of a series (HTTP once when a series window opens, then voice_dub_update). */
export const useVoicesStore = defineStore("voices", () => {
  const bySlot = ref<Record<string, VoiceDub[]>>({})

  function load(seriesId: number, items: VoiceDub[]): void {
    const kept: Record<string, VoiceDub[]> = {}
    for (const [key, list] of Object.entries(bySlot.value)) {
      if (!key.startsWith(`${seriesId}:`)) kept[key] = list
    }
    for (const item of items) {
      const key = slot(seriesId, item.season, item.episode)
      ;(kept[key] ??= []).push(item)
    }
    bySlot.value = kept
  }

  function apply(event: VoiceDubEvent): void {
    const key = slot(event.series_id, event.season, event.episode)
    const list = (bySlot.value[key] ?? []).filter((v) => v.voice_key !== event.voice_key)
    if (event.status !== "removed") {
      // A stale track starts over; a moved one is still in place.
      const status = event.status === "stale" ? "pending" : event.status === "moved" ? "placed" : event.status
      list.push({
        season: event.season,
        episode: event.episode,
        voice_key: event.voice_key,
        voice_label: event.voice_label,
        status,
        detail: event.detail,
      })
      list.sort((a, b) => a.voice_label.localeCompare(b.voice_label))
    }
    bySlot.value = { ...bySlot.value, [key]: list }
  }

  function forEpisode(seriesId: number, season: number, episode: number): VoiceDub[] {
    return bySlot.value[slot(seriesId, season, episode)] ?? []
  }

  return { bySlot, load, apply, forEpisode }
})
