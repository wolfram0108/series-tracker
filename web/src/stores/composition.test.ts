import { beforeEach, describe, expect, it } from "vitest"
import { createPinia, setActivePinia } from "pinia"
import { useCompositionStore } from "./composition"

const reference = (over = {}) => ({
  season: 1, episode: 2, part: null, mark: "by_level" as const, source_id: 7, row_key: "a", player: "vk",
  voice_group: null, height: 1080, reason: null, series_id: 1, ...over,
})

describe("composition store", () => {
  beforeEach(() => setActivePinia(createPinia()))

  it("keeps the layout packet with the greater revision per series", () => {
    const store = useCompositionStore()
    store.applyChanged({ series_id: 1, rev: 5, episodes: [], summary_only: false })
    store.applyChanged({ series_id: 1, rev: 3, episodes: [{ season: 1, episode: 1 }], summary_only: true })
    store.applyChanged({ series_id: 2, rev: 1, episodes: [], summary_only: false })
    expect(store.layoutChanged[1]).toMatchObject({ rev: 5, summary_only: false })
    expect(store.layoutChanged[2].rev).toBe(1)
  })

  it("holds a failure of a recount until the next layout arrives", () => {
    const store = useCompositionStore()
    store.applyChanged({ series_id: 1, rev: 1, episodes: [], summary_only: false })
    store.applyFailed({ series_id: 1, reason: '{"code":"composition_error"}' })
    expect(store.layoutFailed[1].reason).toBe('{"code":"composition_error"}')
    store.applyChanged({ series_id: 1, rev: 2, episodes: [], summary_only: false })
    expect(store.layoutFailed[1]).toBeUndefined()
  })

  it("keeps the last reference packet per episode, not merged with the layout packets", () => {
    const store = useCompositionStore()
    store.applyReference(reference())
    store.applyReference(reference({ episode: 3, mark: "none" }))
    store.applyReference(reference({ mark: "own", height: 720 }))
    expect(store.referenceOf(1, 1, 2)).toMatchObject({ mark: "own", height: 720 })
    expect(store.referenceOf(1, 1, 3)?.mark).toBe("none")
    expect(store.referenceOf(1, 1, 4)).toBeNull()
    expect(store.layoutChanged[1]).toBeUndefined()
  })

  it("keeps the parts of one episode apart", () => {
    const store = useCompositionStore()
    store.applyReference(reference({ part: 1, mark: "own" }))
    store.applyReference(reference({ part: 2, mark: "none" }))
    store.applyReference(reference({ part: null, mark: "by_level" }))
    expect(store.referenceOf(1, 1, 2, 1)?.mark).toBe("own")
    expect(store.referenceOf(1, 1, 2, 2)?.mark).toBe("none")
    expect(store.referenceOf(1, 1, 2)?.mark).toBe("by_level")
  })
})
