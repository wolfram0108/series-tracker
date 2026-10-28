import { beforeEach, describe, expect, it } from "vitest"
import { createPinia, setActivePinia } from "pinia"
import { useVoicesStore, type VoiceDub, type VoiceDubEvent } from "./voices"

const dub = (over: Partial<VoiceDub> = {}): VoiceDub => ({
  season: 1, episode: 2, voice_key: "blackcat", voice_label: "BlackCat", status: "ready", state: "align_queued", detail: null, ...over,
})
const event = (over: Partial<VoiceDubEvent> = {}): VoiceDubEvent => ({ series_id: 7, ...dub(), ...over })

describe("voices store", () => {
  beforeEach(() => setActivePinia(createPinia()))

  it("loads the voice-overs of a series by episode and replaces the previous load", () => {
    const store = useVoicesStore()
    store.load(7, [dub(), dub({ voice_key: "red", voice_label: "RedHeadSound" }), dub({ episode: 3 })])
    expect(store.forEpisode(7, 1, 2).map((v) => v.voice_label)).toEqual(["BlackCat", "RedHeadSound"])
    expect(store.forEpisode(7, 1, 3)).toHaveLength(1)
    store.load(7, [dub({ status: "placed", state: "done" })])
    expect(store.forEpisode(7, 1, 2).map((v) => v.state)).toEqual(["done"])
    expect(store.forEpisode(7, 1, 3)).toEqual([])
  })

  it("keeps other series untouched when one is loaded", () => {
    const store = useVoicesStore()
    store.load(8, [dub()])
    store.load(7, [])
    expect(store.forEpisode(8, 1, 2)).toHaveLength(1)
  })

  it("takes a pushed packet as the whole voice-over and changes one entry in place", () => {
    const store = useVoicesStore()
    store.load(7, [dub(), dub({ voice_key: "red", voice_label: "RedHeadSound" })])
    store.apply(event({ status: "rejected", state: "error", detail: "критично: нет общего звука" }))
    const [black, red] = store.forEpisode(7, 1, 2)
    expect(black).toMatchObject({ voice_label: "BlackCat", status: "rejected", state: "error", detail: "критично: нет общего звука" })
    expect(red).toMatchObject({ status: "ready", state: "align_queued" })
  })

  it("shows a voice-over that arrives by event on an episode that had none", () => {
    const store = useVoicesStore()
    store.apply(event({ episode: 9, status: "ready" }))
    expect(store.forEpisode(7, 1, 9)).toHaveLength(1)
  })

  it("starts a stale track over, keeps a moved one in place and drops a removed one", () => {
    const store = useVoicesStore()
    store.load(7, [dub({ status: "placed", state: "done" })])
    store.apply(event({ status: "moved", state: "done" }))
    expect(store.forEpisode(7, 1, 2)[0]).toMatchObject({ status: "placed", state: "done" })
    store.apply(event({ status: "stale", state: "planned", detail: "референс серии заменён" }))
    expect(store.forEpisode(7, 1, 2)[0]).toMatchObject({ status: "pending", state: "planned", detail: "референс серии заменён" })
    store.apply(event({ status: "removed" }))
    expect(store.forEpisode(7, 1, 2)).toEqual([])
  })
})
