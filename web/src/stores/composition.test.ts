import { beforeEach, describe, expect, it } from "vitest"
import { createPinia, setActivePinia } from "pinia"
import { useCompositionStore } from "./composition"


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


})
