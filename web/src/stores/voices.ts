import { defineStore } from "pinia"
import { ref } from "vue"
import type { components } from "../api/schema"

type Schemas = components["schemas"]

/** One additional voice-over of an episode: its state and the reason of a failure or rejection. */
export type VoiceDub = Schemas["DubItem"] & { job_id?: string | null; file?: string | null }
/** Packet of voice_dub_update: the whole voice-over, so nothing is asked back; the newest (epoch, rev) wins. */
export type VoiceDubEvent = Schemas["DubEvent"]
/** Packet of voice_dubs_snapshot: every voice-over, sent when this interface connects. */
export type VoiceDubsSnapshot = Schemas["DubsSnapshot"]
/** Packet of voice_settings_update: the settings as they apply after a save, whole, so nothing is asked back. */
export type VoiceSettingsView = Schemas["VoiceSettingsView"]

/** The place of a packet in the order of the core: a later start of the module (epoch) outranks any earlier one. */
type Stamp = { epoch: number; rev: number }
const NEVER: Stamp = { epoch: 0, rev: 0 }
const newer = (a: Stamp, b: Stamp) => a.epoch > b.epoch || (a.epoch === b.epoch && a.rev > b.rev)

const slot = (seriesId: number, season: number, episode: number) => `${seriesId}:${season}:${episode}`
const identity = (slotKey: string, voiceKey: string) => `${slotKey}:${voiceKey}`

/** Voice-overs per episode of every series, fed only by the core's pushes (snapshot, then updates). */
export const useVoicesStore = defineStore("voices", () => {
  const bySlot = ref<Record<string, VoiceDub[]>>({})
  // The newest settings the core announced; the settings screen follows it.
  const settings = ref<VoiceSettingsView | null>(null)
  // The newest stamp seen per voice-over; a removed one stays here so that a late packet cannot revive it.
  const stamps = new Map<string, Stamp>()
  // The newest full picture taken in: it also says what did not exist, so a packet not newer than it is dropped.
  let whole: Stamp = NEVER
  // The latest start of the core heard from; whatever an earlier start sent is history.
  let epoch = 0

  const byLabel = (a: VoiceDub, b: VoiceDub) => a.voice_label.localeCompare(b.voice_label)

  function apply(event: VoiceDubEvent): void {
    const key = slot(event.series_id, event.season, event.episode)
    const id = identity(key, event.voice_key)
    const stamp = { epoch: event.epoch, rev: event.rev }
    if (stamp.epoch < epoch || !newer(stamp, whole) || !newer(stamp, stamps.get(id) ?? NEVER)) return
    epoch = stamp.epoch
    stamps.set(id, stamp)
    const list = (bySlot.value[key] ?? []).filter((v) => v.voice_key !== event.voice_key)
    if (event.status !== "removed") {
      list.push({
        season: event.season,
        episode: event.episode,
        voice_key: event.voice_key,
        voice_label: event.voice_label,
        job_id: event.job_id ?? undefined,
        file: event.file ?? undefined,
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
    if (stamp.epoch < epoch || !newer(stamp, whole)) return
    whole = stamp
    epoch = stamp.epoch
    const next: Record<string, VoiceDub[]> = {}
    for (const item of packet.items) {
      const key = slot(item.series_id, item.season, item.episode)
      const id = identity(key, item.voice_key)
      if (newer(stamps.get(id) ?? NEVER, stamp)) continue
      stamps.set(id, stamp)
      ;(next[key] ??= []).push({
        season: item.season,
        episode: item.episode,
        voice_key: item.voice_key,
        voice_label: item.voice_label,
        job_id: item.job_id ?? undefined,
        file: item.file ?? undefined,
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
    // What the picture covers needs no mark of its own: `whole` rejects anything that is not newer than it.
    for (const [id, seen] of [...stamps]) {
      if (!newer(seen, stamp)) stamps.delete(id)
    }
    for (const list of Object.values(next)) list.sort(byLabel)
    bySlot.value = next
  }

  function forEpisode(seriesId: number, season: number, episode: number): VoiceDub[] {
    return bySlot.value[slot(seriesId, season, episode)] ?? []
  }

  /** The name of a voice-over in the alignment queue: the job that aligns it and the file it was sent as; the file
   *  without its extension while the core holds no such voice-over any more. */
  function dubName(jobId: string, file: string): string {
    for (const list of Object.values(bySlot.value)) {
      const found = list.find((dub) => dub.job_id === jobId && dub.file === file)
      if (found) return found.voice_label
    }
    return file.replace(/\.[^.]+$/, "")
  }

  function applySettings(next: VoiceSettingsView): void {
    settings.value = next
  }

  return { bySlot, settings, apply, snapshot, forEpisode, dubName, applySettings }
})
