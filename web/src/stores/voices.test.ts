import { beforeEach, describe, expect, it } from "vitest"
import { createPinia, setActivePinia } from "pinia"
import { useVoicesStore, type VoiceDub, type VoiceDubEvent, type VoiceDubsSnapshot, type VoiceSettingsView } from "./voices"

const dub = (over: Partial<VoiceDub> = {}): VoiceDub => ({
  season: 1, episode: 2, voice_key: "blackcat", voice_label: "BlackCat", status: "ready", state: "align_queued", hint: "ready", detail: null, ...over,
})
const event = (rev: number, over: Partial<VoiceDubEvent> = {}): VoiceDubEvent => ({ series_id: 7, ...dub(), epoch: 1, rev, ...over })
const snapshot = (rev: number, items: (VoiceDub & { series_id?: number })[], epoch = 1): VoiceDubsSnapshot => ({
  epoch,
  rev,
  items: items.map((item) => ({ series_id: 7, ...item })),
})

describe("voices store", () => {
  beforeEach(() => setActivePinia(createPinia()))

  it("takes a snapshot as every voice-over of every series, grouped by episode", () => {
    const store = useVoicesStore()
    store.snapshot(snapshot(10, [dub(), dub({ voice_key: "red", voice_label: "RedHeadSound" }), dub({ episode: 3 }), dub({ series_id: 8 } as never)]))
    expect(store.forEpisode(7, 1, 2).map((v) => v.voice_label)).toEqual(["BlackCat", "RedHeadSound"])
    expect(store.forEpisode(7, 1, 3)).toHaveLength(1)
    expect(store.forEpisode(8, 1, 2)).toHaveLength(1)
  })

  it("replaces what it knew by the next snapshot", () => {
    const store = useVoicesStore()
    store.snapshot(snapshot(10, [dub(), dub({ episode: 3 })]))
    store.snapshot(snapshot(20, [dub({ status: "placed", state: "done" })]))
    expect(store.forEpisode(7, 1, 2).map((v) => v.state)).toEqual(["done"])
    expect(store.forEpisode(7, 1, 3)).toEqual([])
  })

  it("takes a pushed packet as the whole voice-over and changes one entry in place", () => {
    const store = useVoicesStore()
    store.snapshot(snapshot(10, [dub(), dub({ voice_key: "red", voice_label: "RedHeadSound" })]))
    store.apply(event(11, { status: "rejected", state: "error", detail: { code: "critical", params: { text: "нет общего звука" } } }))
    const [black, red] = store.forEpisode(7, 1, 2)
    expect(black).toMatchObject({ voice_label: "BlackCat", status: "rejected", state: "error", detail: { code: "critical", params: { text: "нет общего звука" } } })
    expect(red).toMatchObject({ status: "ready", state: "align_queued" })
  })

  it("shows a voice-over that arrives by event on an episode that had none", () => {
    const store = useVoicesStore()
    store.apply(event(5, { episode: 9, status: "ready" }))
    expect(store.forEpisode(7, 1, 9)).toHaveLength(1)
  })

  it("starts a stale track over, keeps a moved one in place and drops a removed one", () => {
    const store = useVoicesStore()
    store.snapshot(snapshot(10, [dub({ status: "placed", state: "done" })]))
    store.apply(event(11, { status: "moved", state: "done" }))
    expect(store.forEpisode(7, 1, 2)[0]).toMatchObject({ status: "placed", state: "done" })
    store.apply(event(12, { status: "stale", state: "planned", detail: { code: "reference_replaced", params: {} } }))
    expect(store.forEpisode(7, 1, 2)[0]).toMatchObject({ status: "pending", state: "planned", detail: { code: "reference_replaced", params: {} } })
    store.apply(event(13, { status: "removed" }))
    expect(store.forEpisode(7, 1, 2)).toEqual([])
  })

  it("ignores a packet that is older than the one it already shows", () => {
    const store = useVoicesStore()
    store.apply(event(20, { status: "placed", state: "done" }))
    store.apply(event(19, { status: "aligning", state: "running" }))
    expect(store.forEpisode(7, 1, 2)[0]).toMatchObject({ status: "placed", state: "done" })
  })

  it("does not bring a removed voice-over back with a late packet", () => {
    const store = useVoicesStore()
    store.apply(event(10, { status: "ready" }))
    store.apply(event(12, { status: "removed" }))
    store.apply(event(11, { status: "aligning", state: "running" }))
    expect(store.forEpisode(7, 1, 2)).toEqual([])
  })

  it("keeps what a newer packet changed while the snapshot was on its way", () => {
    const store = useVoicesStore()
    store.apply(event(15, { status: "placed", state: "done" }))
    store.apply(event(16, { episode: 4, status: "ready" }))
    store.snapshot(snapshot(14, [dub({ status: "aligning", state: "running" })]))
    expect(store.forEpisode(7, 1, 2)[0]).toMatchObject({ status: "placed", state: "done" })
    expect(store.forEpisode(7, 1, 4)).toHaveLength(1)
  })

  it("lets a snapshot drop a voice-over that was removed before it, but not one removed after it", () => {
    const store = useVoicesStore()
    store.snapshot(snapshot(10, [dub(), dub({ episode: 3 })]))
    store.snapshot(snapshot(20, [dub()]))
    expect(store.forEpisode(7, 1, 3)).toEqual([])
    store.apply(event(30, { status: "removed" }))
    store.snapshot(snapshot(25, [dub()]))
    expect(store.forEpisode(7, 1, 2)).toEqual([])
  })

  it("takes the packets of a later start of the core even when their numbers are lower", () => {
    const store = useVoicesStore()
    store.apply(event(900, { status: "placed", state: "done" }))
    store.snapshot(snapshot(3, [dub({ status: "pending", state: "planned" })], 2))
    expect(store.forEpisode(7, 1, 2)[0]).toMatchObject({ status: "pending", state: "planned" })
    store.apply(event(4, { epoch: 2, status: "downloading", state: "downloading" }))
    expect(store.forEpisode(7, 1, 2)[0]).toMatchObject({ status: "downloading" })
  })

  it("ignores what an earlier start of the core says after a later one has spoken", () => {
    const store = useVoicesStore()
    store.apply(event(1, { epoch: 2, status: "ready" }))
    store.apply(event(900, { epoch: 1, status: "failed", state: "error" }))
    store.snapshot(snapshot(950, [], 1))
    expect(store.forEpisode(7, 1, 2)[0]).toMatchObject({ status: "ready" })
  })
  it("does not bring a removed voice-over back after an empty snapshot", () => {
    const store = useVoicesStore()
    store.apply(event(2, { status: "removed" }))
    store.snapshot(snapshot(3, []))
    store.apply(event(1, { status: "downloading", state: "downloading", hint: "downloading" }))
    expect(store.forEpisode(7, 1, 2)).toEqual([])
  })

  it("holds an empty snapshot of a later run against every packet of an earlier one", () => {
    const store = useVoicesStore()
    store.snapshot(snapshot(1, [], 2))
    store.apply(event(500, { epoch: 1 }))
    expect(store.forEpisode(7, 1, 2)).toEqual([])
    store.apply(event(2, { epoch: 2 }))
    expect(store.forEpisode(7, 1, 2)).toHaveLength(1)
  })

  it("ignores a snapshot that is older than the one it already took in", () => {
    const store = useVoicesStore()
    store.snapshot(snapshot(10, [dub()]))
    store.snapshot(snapshot(5, []))
    expect(store.forEpisode(7, 1, 2)).toHaveLength(1)
  })

  it("lets a packet newer than the last snapshot through and keeps that order after forgetting old marks", () => {
    const store = useVoicesStore()
    store.apply(event(4, { status: "removed" }))
    store.snapshot(snapshot(5, []))
    store.apply(event(7, { status: "ready" }))
    store.apply(event(6, { status: "aligning", state: "running" }))
    expect(store.forEpisode(7, 1, 2)[0]).toMatchObject({ status: "ready" })
  })
  it("drops a packet of an earlier start even about a voice-over the later start has not mentioned", () => {
    const store = useVoicesStore()
    store.apply(event(1, { epoch: 2, voice_key: "red", voice_label: "RedHeadSound" }))
    store.apply(event(900, { epoch: 1 }))
    expect(store.forEpisode(7, 1, 2).map((v) => v.voice_key)).toEqual(["red"])
  })

  it("keeps the newest settings the core announced", () => {
    const store = useVoicesStore()
    const view = (max: number): VoiceSettingsView => ({
      priority: ["kodik"], voice_aliases: {}, player_aliases: {}, temp_root: "/data/tmp", max_temp_episodes: max,
      defaults: { priority: ["kodik"], temp_root: "/data/tmp", max_temp_episodes: 0 }, rejected: [], known_voices: [],
    })
    expect(store.settings).toBeNull()
    store.applySettings(view(1))
    store.applySettings(view(3))
    expect(store.settings?.max_temp_episodes).toBe(3)
  })
})
