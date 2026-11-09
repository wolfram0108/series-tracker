import { describe, it, expect, beforeEach } from "vitest"
import { setActivePinia, createPinia } from "pinia"
import { useSeriesStore, type Series } from "./series"

function mk(id: number, over: Partial<Series> = {}): Series {
  return {
    id,
    name: `Серия ${id}`,
    site: "kinozal.me",
    auto_scan_enabled: true,
    statuses: ["waiting"],
    is_busy: false,
    ...over,
  }
}

describe("seriesStore merge-логика", () => {
  beforeEach(() => setActivePinia(createPinia()))

  it("applyDelta мержит частично, не заменяя объект целиком", () => {
    const store = useSeriesStore()
    store.list = [mk(1, { name: "Старое имя", statuses: ["waiting"] })]
    const ref0 = store.list[0]

    store.applyDelta({ id: 1, statuses: ["downloading"], is_busy: true })

    expect(store.list[0]).toBe(ref0) // тот же объект (Object.assign, не замена)
    expect(store.list[0].statuses).toEqual(["downloading"]) // обновлено
    expect(store.list[0].name).toBe("Старое имя") // не присланное — сохранено
  })

  it("applyDelta игнорирует неизвестный id", () => {
    const store = useSeriesStore()
    store.list = [mk(1)]
    store.applyDelta({ id: 999, statuses: ["error"] })
    expect(store.list[0].statuses).toEqual(["waiting"])
  })

  it("falsy is_busy в дельте снимает флаг сохранения (находка 38)", () => {
    const store = useSeriesStore()
    store.list = [mk(1)]
    store.markSaving(1)
    expect(store.isSaving(1)).toBe(true)

    store.applyDelta({ id: 1, statuses: ["ready"], is_busy: false })
    expect(store.isSaving(1)).toBe(false)
  })

  it("truthy is_busy не снимает флаг сохранения", () => {
    const store = useSeriesStore()
    store.list = [mk(1)]
    store.markSaving(1)
    store.applyDelta({ id: 1, statuses: ["renaming"], is_busy: true })
    expect(store.isSaving(1)).toBe(true)
  })

  it("upsert вставляет новую и заменяет существующую по id", () => {
    const store = useSeriesStore()
    store.list = [mk(1)]
    store.upsert(mk(2, { name: "Вторая" }))
    expect(store.list).toHaveLength(2)
    store.upsert(mk(1, { name: "Обновлённая" }))
    expect(store.list).toHaveLength(2)
    expect(store.byId(1)?.name).toBe("Обновлённая")
  })

  it("remove удаляет серию и снимает флаг сохранения", () => {
    const store = useSeriesStore()
    store.list = [mk(1), mk(2)]
    store.markSaving(1)
    store.remove(1)
    expect(store.list.map((s) => s.id)).toEqual([2])
    expect(store.isSaving(1)).toBe(false)
  })
})

describe("the sources of a series in the store", () => {
  beforeEach(() => setActivePinia(createPinia()))
  const source = (id: number, position: number) =>
    ({ id, series_id: 1, position, url: `https://a/${id}`, source_type: "web_video" as const, color: id, parser_profile_id: null, vibix_season: null })

  it("takes a source added or changed from the packet, in the order of the series, and drops one that went", () => {
    const store = useSeriesStore()
    store.list = [mk(1, { sources: [source(1, 0), source(2, 1)] })]
    store.applySource({ series_id: 1, source_id: 3, source: source(3, 2) })
    store.applySource({ series_id: 1, source_id: 1, source: { ...source(1, 5), url: "https://a/moved" } })
    expect(store.list[0].sources!.map((each) => [each.id, each.url])).toEqual([[2, "https://a/2"], [3, "https://a/3"], [1, "https://a/moved"]])
    store.applySource({ series_id: 1, source_id: 2, source: null })
    expect(store.list[0].sources!.map((each) => each.id)).toEqual([3, 1])
    store.applySource({ series_id: 9, source_id: 3, source: null })
  })
})

