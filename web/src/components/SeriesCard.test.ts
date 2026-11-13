// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest"
import { flushPromises, mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import SeriesCard from "./SeriesCard.vue"
import { useSeriesReadsStore } from "../stores/seriesReads"
import type { Series } from "../stores/series"

const frame = () => new Promise<void>((done) => requestAnimationFrame(() => done()))  // the reads of the layout (lib/layout.ts)

// The pills of the row the user sees (a hidden row measures their widths).
const visible = (w: { findAll: (selector: string) => { element: Element; text: () => string }[] }) =>
  w.findAll(".status-pill").filter((each) => !each.element.closest(".status-pills-measure")).map((each) => each.text())

const series = { id: 40, name: "Владыка", site: "x", statuses: ["scanning"], auto_scan_enabled: false } as unknown as Series

describe("SeriesCard", () => {
  beforeEach(() => setActivePinia(createPinia()))

  it("has a pill with the read of the pages of its sources, whoever asked for it: the one furthest from its end, until the read ends", async () => {
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

  it("shows every status that fits and puts into +N only those the row has no room for, the least important first", async () => {
    // jsdom lays nothing out: the room of the row and the width of each pill are given.
    let room = 500
    const widths = vi.spyOn(HTMLElement.prototype, "offsetWidth", "get").mockImplementation(function (this: HTMLElement) {
      return this.dataset.key === "overflow" ? 40 : 100
    })
    const rooms = vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockImplementation(() => room)
    const four = { ...series, statuses: ["scanning", "aligning", "downloading", "ready"] } as unknown as Series
    const wide = mount(SeriesCard, { props: { series: four } })
    await flushPromises()
    await frame()
    expect(visible(wide)).toEqual(["Готово", "Загрузка", "Выравнивание", "Синхронизация"])
    room = 300  // four pills of 100 do not fit: two and the counter of the other two do
    const narrow = mount(SeriesCard, { props: { series: four } })
    await flushPromises()
    await frame()
    expect(visible(narrow)).toEqual(["Выравнивание", "Синхронизация", "+2"])
    widths.mockRestore()
    rooms.mockRestore()
  })

  it("names an open run a sync and gives slicing, alignment and packing a pill and a layer of their own", () => {
    const working = { ...series, statuses: ["slicing", "aligning", "packing"] } as unknown as Series
    const w = mount(SeriesCard, { props: { series: working } })
    expect(visible(w).sort()).toEqual(
      ["Выравнивание", "Нарезка", "Упаковка"])
    for (const layer of ["slicing", "aligning", "packing"]) {
      expect(w.find(`.layer-${layer}`).attributes("style")).not.toContain("width: 0%")
    }
    expect(w.find(".layer-downloading").attributes("style")).toContain("width: 0%")
    const processing = mount(SeriesCard, { props: { series } })
    expect(visible(processing)).toContain("Синхронизация")
  })
})
