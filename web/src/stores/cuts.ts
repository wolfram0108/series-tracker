import { defineStore } from "pinia"
import { ref } from "vue"
import type { components } from "../api/schema"

type Schemas = components["schemas"]
/** A cut of an episode out of its compilation (stage 12), as `voices` tells it. */
export type Cut = Schemas["CutItem"]
/** Packet of voice_cut_update: the whole cut; the newest (epoch, rev) wins; status `removed` takes it away. */
export type CutEvent = Cut & { epoch: number; rev: number }
/** Packet of voice_cuts_snapshot: every cut, sent when this interface connects. */
export interface CutsSnapshot { epoch: number; rev: number; items: Cut[] }

type Stamp = { epoch: number; rev: number }
const NEVER: Stamp = { epoch: 0, rev: 0 }
const newer = (a: Stamp, b: Stamp) => a.epoch > b.epoch || (a.epoch === b.epoch && a.rev > b.rev)
const keyOf = (cut: Pick<Cut, "series_id" | "season" | "episode" | "part">) =>
  `${cut.series_id}:${cut.season}:${cut.episode}:${cut.part ?? 0}`

/** The cuts of every series, fed only by the core's pushes (snapshot, then updates), as the voice-overs are. */
export const useCutsStore = defineStore("cuts", () => {
  const byKey = ref<Record<string, Cut>>({})
  const stamps = new Map<string, Stamp>()
  let whole: Stamp = NEVER
  let epoch = 0

  function apply(event: CutEvent): void {
    const key = keyOf(event)
    const stamp = { epoch: event.epoch, rev: event.rev }
    if (stamp.epoch < epoch || !newer(stamp, whole) || !newer(stamp, stamps.get(key) ?? NEVER)) return
    epoch = stamp.epoch
    stamps.set(key, stamp)
    const { epoch: _epoch, rev: _rev, ...cut } = event
    const next = { ...byKey.value }
    if (event.status === "removed") delete next[key]
    else next[key] = cut
    byKey.value = next
  }

  function snapshot(packet: CutsSnapshot): void {
    const stamp = { epoch: packet.epoch, rev: packet.rev }
    if (stamp.epoch < epoch || !newer(stamp, whole)) return
    whole = stamp
    epoch = stamp.epoch
    const next: Record<string, Cut> = {}
    for (const item of packet.items) {
      const key = keyOf(item)
      const kept = stamps.get(key)
      next[key] = kept && newer(kept, stamp) && byKey.value[key] ? byKey.value[key] : item
    }
    byKey.value = next
  }

  const forSeries = (seriesId: number): Cut[] => Object.values(byKey.value).filter((cut) => cut.series_id === seriesId)

  return { byKey, apply, snapshot, forSeries }
})
