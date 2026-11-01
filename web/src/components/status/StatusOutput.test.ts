// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { flushPromises, mount, type VueWrapper } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import StatusOutput from "./StatusOutput.vue"
import { SPECS } from "../../lib/output.fixtures"
import * as calls from "../../composables/outputCalls"
import type { Composition } from "../../composables/outputCalls"
import type { Answer } from "../../composables/sourceCalls"
import { useSSE } from "../../composables/useSSE"
import { t } from "../../i18n"
import type { Reference, Row } from "../../lib/output"

vi.mock("../../composables/outputCalls")

const ok = <T>(data: T): Answer<T> => ({ ok: true, data })

const row = (season: number, episode: number, over: Partial<Row> = {}): Row => ({
  series_id: 1, source_id: 7, row_key: `r${season}-${episode}`, excluded: false, season, episode_start: episode,
  episode_end: episode, voice_label: "Ruchime", voice_name: "Ruchime", player: "vibix", player_label: "Vibix",
  group: "", text: "", heights: [1080], state: "playable", link: "", ...over,
})
const reference = (season: number, episode: number, over: Partial<Reference> = {}): Reference => ({
  season, episode, part: null, mark: "by_level", source_id: 7, row_key: `r${season}-${episode}`, player: "vibix",
  voice_group: "Ruchime", height: 1080, reason: null, ...over,
})
const composition = (): Composition => ({
  series_id: 1, rev: 1, sources: [], settings: [],
  rows: [row(1, 1), row(1, 2), row(2, 1)],
  levels: [{ season: null, levers: [] }, { season: 1, levers: [] }, { season: 2, levers: [] }],
  levers: SPECS,
})

let mounted: VueWrapper[] = []
const mountOutput = () => {
  const wrapper = mount(StatusOutput, { props: { seriesId: 1, seriesName: "Тайтл" } })
  mounted.push(wrapper)
  return wrapper
}

beforeEach(() => {
  setActivePinia(createPinia())
  useSSE().connected.value = false
  vi.mocked(calls.fetchComposition).mockReset().mockResolvedValue(ok(composition()))
  vi.mocked(calls.fetchOutput).mockReset().mockResolvedValue(ok([
    reference(1, 1), reference(1, 2, { mark: "own" }), reference(2, 1)]))
  vi.mocked(calls.switchOutput).mockReset().mockResolvedValue(ok({ success: true }))
  vi.mocked(calls.syncSeries).mockReset().mockResolvedValue(ok({ success: true }))
  vi.mocked(calls.reprocessSeries).mockReset().mockResolvedValue(ok({ success: true }))
  vi.mocked(calls.adoptSeries).mockReset().mockResolvedValue(ok({ success: true }))
  vi.mocked(calls.setLever).mockReset().mockResolvedValue(ok({ success: true }))
  vi.mocked(calls.fetchRenames).mockReset().mockResolvedValue(ok({
    preview: [{ unique_id: "u2", season: 1, episode: 2, part: null, new_filename_preview: "Show s01e02.mp4" }] }))
})

afterEach(() => mounted.splice(0).forEach((wrapper) => wrapper.unmount()))

describe("StatusOutput", () => {
  it("shows the series, its seasons and their episodes as the core gave them", async () => {
    const w = mountOutput()
    expect(w.text()).toContain(t("output.loading"))
    await flushPromises()
    expect(w.find("[data-scope=series] .level-title").text()).toBe("Тайтл")
    expect(w.findAll("[data-scope=season] > header .level-title").map((each) => each.text())).toEqual([
      t("output.season", { n: 1 }), t("output.season", { n: 2 })])
    expect(w.findAll("article").map((each) => [each.attributes("data-season"), each.attributes("data-episode")])).toEqual([
      ["1", "1"], ["1", "2"], ["2", "1"]])
  })

  it("says that there are no episodes yet when the layout has none", async () => {
    vi.mocked(calls.fetchComposition).mockResolvedValue(ok({ ...composition(), rows: [] }))
    vi.mocked(calls.fetchOutput).mockResolvedValue(ok([]))
    const w = mountOutput()
    await flushPromises()
    expect(w.text()).toContain(t("output.empty"))
    expect(w.find("article").exists()).toBe(false)
  })

  it("keeps only the episodes that depart from the norm, and the seasons that have any, when the filter is on", async () => {
    const w = mountOutput()
    await flushPromises()
    await w.find(".output-deviations input").setValue(true)
    expect(w.findAll("article").map((each) => each.attributes("data-episode"))).toEqual(["2"])
    expect(w.findAll("[data-scope=season]")).toHaveLength(1)
    await w.find(".output-deviations input").setValue(false)
    expect(w.findAll("article")).toHaveLength(3)
  })

  it("switches an episode off, and a season, by the numbers the core gave", async () => {
    const w = mountOutput()
    await flushPromises()
    await w.findAll("article")[1].find(".episode-switch").trigger("click")
    await w.findAll("[data-scope=season]")[1].find(".level-switch").trigger("click")
    expect(vi.mocked(calls.switchOutput).mock.calls).toEqual([
      [1, { season: 1, episode: 2, off: true }], [1, { season: 2, off: true }]])
  })

  it("renders the controls anew and shows the words of a refusal, so that they show what the core holds", async () => {
    vi.mocked(calls.switchOutput).mockResolvedValueOnce({ ok: false, text: "Сериал не найден" })
    const w = mountOutput()
    await flushPromises()
    const before = w.find("article").element
    await w.find("article .episode-switch").trigger("click")
    await flushPromises()
    expect(w.find(".output-failure").text()).toBe("Сериал не найден")
    expect(w.find("article").element).not.toBe(before)
  })

  it("shows the names the files would get under their episodes when the box is checked, and takes them back when it is not", async () => {
    const w = mountOutput()
    await flushPromises()
    expect(calls.fetchRenames).not.toHaveBeenCalled()
    await w.find(".output-names input").setValue(true)
    await flushPromises()
    const named = w.findAll(".output-episode").map((each) => each.find(".episode-file").exists())
    expect(named).toEqual([false, true, false])
    expect(w.findAll(".episode-file")[0].text()).toBe(t("output.file", { name: "Show s01e02.mp4" }))
    await w.find(".output-names input").setValue(false)
    expect(w.find(".episode-file").exists()).toBe(false)
  })

  it("asks for a pass over the series, the renaming and the adoption by their buttons, each waiting for its own answer", async () => {
    let finish!: (value: Answer<unknown>) => void
    vi.mocked(calls.syncSeries).mockImplementationOnce(() => new Promise((resolve) => { finish = resolve }))
    const w = mountOutput()
    await flushPromises()
    const [sync, reprocess, adopt] = w.findAll(".output-bar button")
    await sync.trigger("click")
    expect(sync.attributes("disabled")).toBeDefined()
    expect(reprocess.attributes("disabled")).toBeUndefined()
    finish(ok({ success: true }))
    await flushPromises()
    expect(sync.attributes("disabled")).toBeUndefined()
    await reprocess.trigger("click")
    await adopt.trigger("click")
    expect(calls.syncSeries).toHaveBeenCalledWith(1)
    expect(calls.reprocessSeries).toHaveBeenCalledWith(1)
    expect(calls.adoptSeries).toHaveBeenCalledWith(1)
  })
})
