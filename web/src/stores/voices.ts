import { defineStore } from "pinia"
import { ref } from "vue"
import type { VoiceReason } from "../lib/voiceReason"

/** One additional voice-over of an episode: its state and the reason of a failure or rejection. */
export interface VoiceDub {
  season: number
  episode: number
  voice_key: string
  voice_label: string
  /** The step behind the state (downloading, aligning, placed, ...). */
  status: string
  /** What the user sees: planned, queued, running, done or error. */
  state: string
  detail: VoiceReason | null
}

/** Packet of voice_dub_update: the whole voice-over, so nothing is asked back. */
export interface VoiceDubEvent extends VoiceDub {
  series_id: number
  /** Grows with every packet of the core: the newest one wins whatever the order of arrival. */
  rev: number
}

/** Packet of voice_dubs_snapshot: every voice-over, sent when this interface connects. */
export interface VoiceDubsSnapshot {
  rev: number
  items: (VoiceDub & { series_id: number })[]
}

const slot = (seriesId: number, season: number, episode: number) => `${seriesId}:${season}:${episode}`
const identity = (slotKey: string, voiceKey: string) => `${slotKey}:${voiceKey}`

/** Voice-overs per episode of every series, fed only by the core's pushes (snapshot, then updates). */
export const useVoicesStore = defineStore("voices", () => {
  const bySlot = ref<Record<string, VoiceDub[]>>({})
  // The newest revision seen per voice-over; a removed one stays here so that a late packet cannot revive it.
  const revs = new Map<string, number>()

  const byLabel = (a: VoiceDub, b: VoiceDub) => a.voice_label.localeCompare(b.voice_label)

  function apply(event: VoiceDubEvent): void {
    const key = slot(event.series_id, event.season, event.episode)
    const id = identity(key, event.voice_key)
    if (event.rev <= (revs.get(id) ?? 0)) return
    revs.set(id, event.rev)
    const list = (bySlot.value[key] ?? []).filter((v) => v.voice_key !== event.voice_key)
    if (event.status !== "removed") {
      list.push({
        season: event.season,
        episode: event.episode,
        voice_key: event.voice_key,
        voice_label: event.voice_label,
        status: event.status === "stale" ? "pending" : event.status === "moved" ? "placed" : event.status,
        state: event.state,
        detail: event.detail,
      })
      list.sort(byLabel)
    }
    bySlot.value = { ...bySlot.value, [key]: list }
  }

  /** Replaces what is known by the snapshot, except what a newer packet has already changed. */
  function snapshot(packet: VoiceDubsSnapshot): void {
    const next: Record<string, VoiceDub[]> = {}
    const shown = new Set<string>()
    for (const item of packet.items) {
      const key = slot(item.series_id, item.season, item.episode)
      const id = identity(key, item.voice_key)
      shown.add(id)
      if ((revs.get(id) ?? 0) > packet.rev) continue
      revs.set(id, packet.rev)
      ;(next[key] ??= []).push({
        season: item.season,
        episode: item.episode,
        voice_key: item.voice_key,
        voice_label: item.voice_label,
        status: item.status,
        state: item.state,
        detail: item.detail,
      })
    }
    for (const [key, list] of Object.entries(bySlot.value)) {
      for (const dub of list) {
        if ((revs.get(identity(key, dub.voice_key)) ?? 0) > packet.rev) (next[key] ??= []).push(dub)
      }
    }
    for (const [id, rev] of [...revs]) {
      if (rev <= packet.rev && !shown.has(id)) revs.delete(id)
    }
    for (const list of Object.values(next)) list.sort(byLabel)
    bySlot.value = next
  }

  function forEpisode(seriesId: number, season: number, episode: number): VoiceDub[] {
    return bySlot.value[slot(seriesId, season, episode)] ?? []
  }

  return { bySlot, apply, snapshot, forEpisode }
})
