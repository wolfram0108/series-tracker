// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from "vitest"
import { flushPromises, mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import SeriesCard from "./SeriesCard.vue"
import { useSeriesReadsStore } from "../stores/seriesReads"
import type { Series } from "../stores/series"

const series = { id: 40, name: "Владыка", site: "x", statuses: ["scanning"], auto_scan_enabled: false } as unknown as Series

describe("SeriesCard", () => {
  beforeEach(() => setActivePinia(createPinia()))

  it("has a pill with the read of the site its scan waits for, the one furthest from its end, until the wait ends", async () => {
    const reads = useSeriesReadsStore()
    const w = mount(SeriesCard, { props: { series } })
    const pill = () => w.findAll(".status-pill").map((each) => each.text()).find((text) => text.includes(" из "))
    expect(pill()).toBeUndefined()
    reads.apply({ series_id: 40, source_id: 28, url: "https://1.ruchime.org/r", phase: "episodes", current: 34, total: 391,
      rate: 0.6 })
    reads.apply({ series_id: 40, source_id: 2, url: "https://vkvideo.ru/@c|q|search", phase: "videos", current: 9, total: 10 })
    await flushPromises()
    expect(pill()).toBe("Страницы серий: 34 из 391 | 0.6 с/с")
    reads.apply({ series_id: 40, source_id: 28, url: "https://1.ruchime.org/r", active: false })
    reads.apply({ series_id: 40, source_id: 2, url: "https://vkvideo.ru/@c|q|search", active: false })
    await flushPromises()
    expect(pill()).toBeUndefined()
  })

  it("names an open run processing and gives slicing, alignment and packing a pill and a layer of their own", () => {
    const working = { ...series, statuses: ["slicing", "aligning", "packing"] } as unknown as Series
    const w = mount(SeriesCard, { props: { series: working } })
    expect(w.findAll(".status-pill").map((each) => each.text()).sort()).toEqual(
      ["Выравнивание", "Нарезка", "Упаковка"])
    for (const layer of ["slicing", "aligning", "packing"]) {
      expect(w.find(`.layer-${layer}`).attributes("style")).not.toContain("width: 0%")
    }
    expect(w.find(".layer-downloading").attributes("style")).toContain("width: 0%")
    const processing = mount(SeriesCard, { props: { series } })
    expect(processing.findAll(".status-pill").map((each) => each.text())).toContain("Обработка")
  })
})
