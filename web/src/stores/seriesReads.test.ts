import { beforeEach, describe, expect, it } from "vitest"
import { createPinia, setActivePinia } from "pinia"
import { useSeriesReadsStore } from "./seriesReads"

const PAGE = "https://1.ruchime.org/release/test"
const VK = "https://vkvideo.ru/@pvashow|Почтенный мастер|search"

describe("series reads store", () => {
  beforeEach(() => setActivePinia(createPinia()))

  it("follows the reads a scan waits for by series and source and drops one when its wait ends", () => {
    const store = useSeriesReadsStore()
    store.apply({ series_id: 40, source_id: 28, url: PAGE, phase: "episodes", current: 300, total: 1500, rate: 2.5 })
    store.apply({ series_id: 40, source_id: 2, url: VK, phase: "videos", current: 10, total: 20 })
    expect(store.forSeries(40).map((read) => read.sourceId).sort()).toEqual([2, 28])
    expect(store.lead(40)).toMatchObject({ sourceId: 28, current: 300, total: 1500, rate: 2.5 })
    store.apply({ series_id: 40, source_id: 28, url: PAGE, active: false })
    expect(store.lead(40)).toMatchObject({ sourceId: 2, rate: null })
    store.apply({ series_id: 40, source_id: 2, url: VK, active: false })
    expect(store.forSeries(40)).toEqual([])
    expect(store.lead(40)).toBeNull()
    expect(store.reads).toEqual({})
  })

  it("keeps the reads of other series apart and forgets everything after a break of the connection", () => {
    const store = useSeriesReadsStore()
    store.apply({ series_id: 40, source_id: 28, url: PAGE, phase: "episodes", current: 1, total: 2 })
    store.apply({ series_id: 46, source_id: 3, url: VK, phase: "videos", current: 1, total: 9 })
    expect(store.lead(46)).toMatchObject({ sourceId: 3 })
    store.apply({ series_id: 46, source_id: 3, url: VK, active: false })
    expect(store.lead(40)).toMatchObject({ sourceId: 28 })
    store.reset()
    expect(store.lead(40)).toBeNull()
  })
})
