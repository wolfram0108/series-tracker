import { defineStore } from "pinia"
import { ref } from "vue"
import type { components } from "../api/schema"

type Schemas = components["schemas"]

/** One additional voice-over of an episode: its state and the reason of a failure or rejection. */
export type VoiceDub = Schemas["DubItem"]
/** Packet of voice_dub_update: the whole voice-over, so nothing is asked back; the newest (epoch, rev) wins. */
export type VoiceDubEvent = Schemas["DubEvent"]
/** Packet of voice_dubs_snapshot: every voice-over, sent when this interface connects. */
export type VoiceDubsSnapshot = Schemas["DubsSnapshot"]

/** The place of a packet in the order of the core: a later start of the module (epoch) outranks any earlier one. */
type Stamp = { epoch: number; rev: number }
const NEVER: Stamp = { epoch: 0, rev: 0 }
const newer = (a: Stamp, b: Stamp) => a.epoch > b.epoch || (a.epoch === b.epoch && a.rev > b.rev)

const slot = (seriesId: number, season: number, episode: number) => `${seriesId}:${season}:${episode}`
const identity = (slotKey: string, voiceKey: string) => `${slotKey}:${voiceKey}`

/** Voice-overs per episode of every series, fed only by the core's pushes (snapshot, then updates). */
export const useVoicesStore = defineStore("voices", () => {
  const bySlot = ref<Record<string, VoiceDub[]>>({})
  // The newest stamp seen per voice-over; a removed one stays here so that a late packet cannot revive it.
  const stamps = new Map<string, Stamp>()

  const byLabel = (a: VoiceDub, b: VoiceDub) => a.voice_label.localeCompare(b.voice_label)

  function apply(event: VoiceDubEvent): void {
    const key = slot(event.series_id, event.season, event.episode)
    const id = identity(key, event.voice_key)
    const stamp = { epoch: event.epoch, rev: event.rev }
    if (!newer(stamp, stamps.get(id) ?? NEVER)) return
    stamps.set(id, stamp)
    const list = (bySlot.value[key] ?? []).filter((v) => v.voice_key !== event.voice_key)
    if (event.status !== "removed") {
      list.push({
        season: event.season,
        episode: event.episode,
        voice_key: event.voice_key,
        voice_label: event.voice_label,
        status: event.status === "stale" ? "pending" : event.status === "moved" ? "placed" : event.status,
        state: event.state,
        hint: event.hint,
        detail: event.detail,
      })
      list.sort(byLabel)
    }
    bySlot.value = { ...bySlot.value, [key]: list }
  }

  /** Replaces what is known by the snapshot, except what a newer packet has already changed. */
  function snapshot(packet: VoiceDubsSnapshot): void {
    const stamp = { epoch: packet.epoch, rev: packet.rev }
    const next: Record<string, VoiceDub[]> = {}
    const shown = new Set<string>()
    for (const item of packet.items) {
      const key = slot(item.series_id, item.season, item.episode)
      const id = identity(key, item.voice_key)
      shown.add(id)
      if (newer(stamps.get(id) ?? NEVER, stamp)) continue
      stamps.set(id, stamp)
      ;(next[key] ??= []).push({
        season: item.season,
        episode: item.episode,
        voice_key: item.voice_key,
        voice_label: item.voice_label,
        status: item.status,
        state: item.state,
        hint: item.hint,
        detail: item.detail,
      })
    }
    for (const [key, list] of Object.entries(bySlot.value)) {
      for (const dub of list) {
        if (newer(stamps.get(identity(key, dub.voice_key)) ?? NEVER, stamp)) (next[key] ??= []).push(dub)
      }
    }
    for (const [id, seen] of [...stamps]) {
      if (!newer(seen, stamp) && !shown.has(id)) stamps.delete(id)
    }
    for (const list of Object.values(next)) list.sort(byLabel)
    bySlot.value = next
  }

  function forEpisode(seriesId: number, season: number, episode: number): VoiceDub[] {
    return bySlot.value[slot(seriesId, season, episode)] ?? []
  }

  return { bySlot, apply, snapshot, forEpisode }
})
