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
