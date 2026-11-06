// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { flushPromises, mount, type VueWrapper } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import StatusOutput from "./StatusOutput.vue"
import { AVAILABILITY, SPECS } from "../../lib/output.fixtures"
import * as calls from "../../composables/outputCalls"
import type { Composition, Queues } from "../../composables/outputCalls"
import type { Answer } from "../../composables/sourceCalls"
import { useSSE } from "../../composables/useSSE"
import { useCompositionStore } from "../../stores/composition"
import { useSeriesStore } from "../../stores/series"
import { useSeriesReadsStore } from "../../stores/seriesReads"
import { useVoicesStore } from "../../stores/voices"
import { t } from "../../i18n"
import type { Reference, Row } from "../../lib/output"

vi.mock("../../composables/outputCalls")

const ok = <T>(data: T): Answer<T> => ({ ok: true, data })

const row = (season: number, episode: number, over: Partial<Row> = {}): Row => ({
  series_id: 1, source_id: 7, row_key: `r${season}-${episode}`, excluded: false, season, episode_start: episode,
  episode_end: episode, voice_label: "Ruchime", voice_name: "Ruchime", player: "vibix", player_label: "Vibix",
  group: "", text: "", heights: [1080], state: "playable", link: "", video: "", ...over,
})
const reference = (season: number, episode: number, over: Partial<Reference> = {}): Reference => ({
  season, episode, part: null, mark: "by_level", source_id: 7, row_key: `r${season}-${episode}`, player: "vibix",
  voice_group: "Ruchime", height: 1080, reason: null, ...over,
})
const composition = (): Composition => ({
  series_id: 1, rev: 1, sources: [], settings: [],
  rows: [row(1, 1), row(1, 2), row(2, 1), row(1, 1, { row_key: "k1", source_id: 8, player: "kodik", player_label: "Kodik", voice_label: "AniStar" })],
  levels: [{ season: null, levers: [] }, { season: 1, levers: [] }, { season: 2, levers: [] }],
  levers: SPECS, availability: AVAILABILITY,
  voice_orders: [{ season: 1, voices: [{ label: "Ruchime", episodes: "1–2" }, { label: "AniStar", episodes: "1" }] },
    { season: 2, voices: [{ label: "Ruchime", episodes: "1" }] }],
})
const queues = (): Queues => ({
  series_id: 1, season: 1, episode: 1,
  reference: { order_level: "default", rows: [
    { group: "Ruchime", source_id: 7, player: "vibix", player_label: "Vibix", height: 1080, episodes: "1", allowed: true, allowed_level: "default" },
    { group: "AniStar", source_id: 8, player: "kodik", player_label: "Kodik", height: 720, episodes: "1", allowed: true, allowed_level: "default" },
  ] },
  groups: [{ group: "AniStar", take: true, take_level: "default", order_level: "default", rows: [
    { group: "AniStar", source_id: 8, player: "kodik", player_label: "Kodik", height: 720, episodes: "1", allowed: true, allowed_level: "default" },
  ] }],
})

// The grid measures itself: here it has room for six of the smallest cards.
function roomFor(cards: number): void {
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
    const width = this.classList.contains("grid-ruler") ? 150 : this.classList.contains("season-grid") ? 150 * cards : 0
    return { width, height: 0, top: 0, left: 0, right: width, bottom: 0, x: 0, y: 0, toJSON: () => ({}) } as DOMRect
  })
}

let mounted: VueWrapper[] = []
const mountOutput = () => {
  const wrapper = mount(StatusOutput, { props: { seriesId: 1, seriesName: "Тайтл" }, attachTo: document.body })
  mounted.push(wrapper)
  return wrapper
}
const cards = (w: VueWrapper) => w.findAll(".episode-card").map((each) => [each.attributes("data-season"), each.attributes("data-episode")])
const button = (w: VueWrapper, text: string) => w.findAll("button").find((each) => each.text().includes(text))

// The settings window measures its tabs; the test has no layout, so the measuring is a stand-in that sees nothing.
class NoLayout {
  observe() {}
  disconnect() {}
}

beforeEach(() => {
  vi.stubGlobal("ResizeObserver", NoLayout)
  Element.prototype.scrollIntoView = () => {}
  setActivePinia(createPinia())
  useSSE().connected.value = false
  roomFor(6)
  vi.mocked(calls.fetchComposition).mockReset().mockResolvedValue(ok(composition()))
  vi.mocked(calls.fetchOutput).mockReset().mockResolvedValue(ok([
    reference(1, 1), reference(1, 2, { mark: "own" }), reference(2, 1)]))
  vi.mocked(calls.fetchMedia).mockReset().mockResolvedValue(ok([]))
  vi.mocked(calls.fetchQueues).mockReset().mockResolvedValue(ok(queues()))
  for (const each of [calls.switchOutput, calls.syncSeries, calls.setLever, calls.resetLever, calls.retryDubs, calls.redoDubs,
    calls.refetchReference]) vi.mocked(each).mockReset().mockResolvedValue(ok({ success: true }))
  vi.mocked(calls.resetFailures).mockReset().mockResolvedValue(ok({ success: true, reset: 1 }))
  vi.mocked(calls.fetchRenames).mockReset().mockResolvedValue(ok({
    preview: [{ unique_id: "u2", season: 1, episode: 2, part: null, new_filename_preview: "Show s01e02.mp4" }] }))
})
afterEach(() => {
  mounted.splice(0).forEach((wrapper) => wrapper.unmount())
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe("StatusOutput", () => {
  it("shows the seasons and their episodes as cards, each with the source and the resolution of its reference", async () => {
    const w = mountOutput()
    expect(w.text()).toContain(t("output.loading"))
    await flushPromises()
    expect(w.findAll(".season-title").map((each) => each.text())).toEqual([t("output.season", { n: 1 }), t("output.season", { n: 2 })])
    expect(cards(w)).toEqual([["1", "1"], ["1", "2"], ["2", "1"]])
    expect(w.find(".episode-card .card-ref").text()).toContain(`Vibix ${t("output.height", { height: 1080 })}`)
    expect(w.findAll(".episode-card")[0].findAll(".track-cell").map((cell) => cell.text())).toEqual(["1", "2"])
  })

  it("puts as many cards in a row as the tracks and the number of episodes of the season allow", async () => {
    const w = mountOutput()
    await flushPromises()
    // Two episodes of a season of two tracks ask for one card a row, though six would fit.
    expect((w.find(".season-grid").element as HTMLElement).style.gridTemplateColumns).toBe("repeat(1, minmax(0, 1fr))")
  })

  it("says that the layout was not counted and the earlier one stands, until a newer layout comes", async () => {
    const w = mountOutput()
    await flushPromises()
    const store = useCompositionStore()
    store.applyFailed({ series_id: 1, reason: JSON.stringify({ code: "composition_error" }) })
    await flushPromises()
    expect(w.find(".output-layout-failure").text()).toContain("Раскладка не пересчитана, действует прежняя")
    expect(w.findAll(".episode-card")).toHaveLength(3)
    store.applyChanged({ series_id: 1, rev: 2, episodes: [], summary_only: false })
    await flushPromises()
    expect(w.find(".output-layout-failure").exists()).toBe(false)
  })

  it("says that there are no episodes yet when the layout has none", async () => {
    vi.mocked(calls.fetchComposition).mockResolvedValue(ok({ ...composition(), rows: [] }))
    vi.mocked(calls.fetchOutput).mockResolvedValue(ok([]))
    const w = mountOutput()
    await flushPromises()
    expect(w.text()).toContain(t("output.empty"))
    expect(w.find(".episode-card").exists()).toBe(false)
  })

  it("counts the episodes of each filter and keeps only those of the one chosen, and the seasons that have any", async () => {
    const w = mountOutput()
    await flushPromises()
    const filter = w.findAll(".output-filter")
    expect(filter.map((each) => each.find("strong").text())).toEqual(["3", "1", "0", "0", "0", "0"])
    await w.find(".output-filter.is-deviations").trigger("click")
    expect(cards(w)).toEqual([["1", "2"]])
    expect(w.findAll(".season-block")).toHaveLength(1)
    await w.find(".output-filter.is-all").trigger("click")
    expect(w.findAll(".episode-card")).toHaveLength(3)
  })

  it("counts as partial an episode done but for a voice-over no source has, and keeps it under its own filter", async () => {
    useVoicesStore().snapshot({ items: [
      { series_id: 1, season: 1, episode: 1, part: null, voice_key: "AniStar", voice_label: "AniStar", status: "absent",
        state: "absent", hint: "absent", detail: null, source_id: null, player: null },
    ], epoch: 1, rev: 1 } as never)
    vi.mocked(calls.fetchMedia).mockResolvedValue(ok([{ unique_id: "u1", source_id: 7, row_key: "r1-1", status: "completed" }]))
    const w = mountOutput()
    await flushPromises()
    expect(w.find(".output-filter.is-partial strong").text()).toBe("1")
    await w.find(".output-filter.is-partial").trigger("click")
    expect(cards(w)).toEqual([["1", "1"]])
    expect(w.find(".episode-card").classes()).toContain("is-partial")
  })

  it("turns an episode off with the switch in the corner of its card, without opening it", async () => {
    const w = mountOutput()
    await flushPromises()
    await w.findAll(".episode-card")[1].find(".power-switch input").setValue(false)
    await flushPromises()
    expect(calls.switchOutput).toHaveBeenCalledWith(1, { season: 1, episode: 2, off: true })
    expect(w.find(".episode-detail").exists()).toBe(false)
  })

  it("opens an episode below its row with its tracks, and asks for the queues of that episode", async () => {
    const w = mountOutput()
    await flushPromises()
    await w.findAll(".episode-card")[0].trigger("click")
    await flushPromises()
    expect(calls.fetchQueues).toHaveBeenCalledWith(1, 1, 1)
    const detail = w.find(".episode-detail")
    expect(detail.attributes("data-episode")).toBe("1")
    expect(detail.findAll(".track-table tbody tr").map((each) => each.attributes("data-group"))).toEqual(["Ruchime", "AniStar"])
    await detail.find(".icon-btn[aria-label='" + t("output.episode.close") + "']").trigger("click")
    expect(w.find(".episode-detail").exists()).toBe(false)
  })

  it("does the action a track asks for: a failed voice-over again, a placed one anew, the reference downloaded again", async () => {
    useVoicesStore().snapshot({ items: [
      { series_id: 1, season: 1, episode: 1, part: null, voice_key: "AniStar", voice_label: "AniStar", status: "failed",
        state: "error", hint: null, detail: { code: "download_failed", params: {} }, source_id: 8, player: "kodik" },
    ], epoch: 1, rev: 1 } as never)
    vi.mocked(calls.fetchMedia).mockResolvedValue(ok([{ unique_id: "u1", source_id: 7, row_key: "r1-1", status: "completed" }]))
    const w = mountOutput()
    await flushPromises()
    await w.findAll(".episode-card")[0].trigger("click")
    await flushPromises()
    await w.find(".track-action.do-retry").trigger("click")
    await w.find(".track-action.do-refetch").trigger("click")
    expect(calls.retryDubs).toHaveBeenCalledWith(1, { season: 1, episode: 1, voice_label: "AniStar" })
    expect(calls.refetchReference).toHaveBeenCalledWith(1, { season: 1, episode: 1, part: null })
  })

  it("sends the order the user dragged a queue into, and a variant left out, as levers of the episode", async () => {
    const w = mountOutput()
    await flushPromises()
    await w.findAll(".episode-card")[0].trigger("click")
    await flushPromises()
    await button(w, t("output.tab.reference"))!.trigger("click")
    const rows = w.findAll(".variant-row")
    expect(rows.map((each) => each.find(".queue-player").text())).toEqual(["Vibix", "Kodik"])
    await rows[1].trigger("dragstart")
    await rows[0].trigger("dragover")
    await rows[0].trigger("drop")
    await rows[0].find("input[type=checkbox]").setValue(false)
    expect(vi.mocked(calls.setLever).mock.calls).toEqual([
      [1, { season: 1, episode: 1, lever: "reference.order", target: null, value: [
        { group: "AniStar", source_id: 8, player: "kodik" }, { group: "Ruchime", source_id: 7, player: "vibix" }] }],
      [1, { season: 1, episode: 1, lever: "reference.allow", target: 'variant:["Ruchime",7,"vibix"]', value: false }],
    ])
  })

  it("opens the settings of the series as the same queues for the whole series", async () => {
    const w = mountOutput()
    await flushPromises()
    await w.find(".output-series-settings").trigger("click")
    await flushPromises()
    expect(calls.fetchQueues).toHaveBeenCalledWith(1, null, null)
  })

  it("switches an episode off from its detail, and a season from its header", async () => {
    const w = mountOutput()
    await flushPromises()
    await w.findAll(".episode-card")[1].trigger("click")
    await flushPromises()
    await w.find(".episode-detail .power-switch input").setValue(false)
    await w.findAll(".season-head .power-switch input")[1].setValue(false)
    expect(vi.mocked(calls.switchOutput).mock.calls).toEqual([
      [1, { season: 1, episode: 2, off: true }], [1, { season: 2, off: true }]])
  })

  it("shows the words of a refusal", async () => {
    vi.mocked(calls.switchOutput).mockResolvedValueOnce({ ok: false, text: "Сериал не найден" })
    const w = mountOutput()
    await flushPromises()
    await w.findAll(".season-head .power-switch input")[0].setValue(false)
    await flushPromises()
    expect(w.find(".output-failure").text()).toBe("Сериал не найден")
  })

  it("shows the name the file gets in the first line of a card wider than the smallest, always", async () => {
    const w = mountOutput()
    await flushPromises()
    expect(w.findAll(".episode-card").map((each) => each.find(".card-name").exists())).toEqual([false, true, false])
    expect(w.find(".card-name").text()).toBe("Show s01e02.mp4")
  })

  it("leaves the name out of the smallest card", async () => {
    roomFor(1)
    const w = mountOutput()
    await flushPromises()
    expect(w.find(".card-name").exists()).toBe(false)
  })

  it("puts the buttons first, then the sources, then the filters and the view", async () => {
    const w = mountOutput()
    await flushPromises()
    const bars = w.findAll(".output-bar")
    expect(bars[0].find(".output-sync").exists() && bars[0].find(".output-series-settings").exists()).toBe(true)
    expect(bars[bars.length - 1].find(".output-filters").exists() && bars[bars.length - 1].find(".output-view").exists()).toBe(true)
  })

  it("has the sync of the series and the reset of failed attempts, each waits for its answer", async () => {
    let finish!: (value: Answer<unknown>) => void
    vi.mocked(calls.syncSeries).mockImplementationOnce(() => new Promise((resolve) => { finish = resolve }))
    const w = mountOutput()
    await flushPromises()
    const sync = w.find(".output-sync")
    await sync.trigger("click")
    expect(sync.attributes("disabled")).toBeDefined() // one pass at a time
    finish(ok({ success: true }))
    await flushPromises()
    expect(sync.attributes("disabled")).toBeUndefined()
    await w.find(".output-retry-failed").trigger("click")
    await flushPromises()
    expect(calls.syncSeries).toHaveBeenCalledWith(1)
    expect(calls.resetFailures).toHaveBeenCalledWith(1)
  })

  it("draws the button of the pass by the table of the core and the state the core reports, not only by its own call", async () => {
    const store = useSeriesStore()
    store.upsert({ id: 1, name: "Тайтл", site: "x", auto_scan_enabled: true, statuses: ["scanning"] })
    const w = mountOutput()
    await flushPromises()
    const sync = w.find(".output-sync")
    expect(sync.attributes("disabled")).toBeDefined()
    store.applyDelta({ id: 1, statuses: [], is_busy: false })
    await flushPromises()
    expect(sync.attributes("disabled")).toBeUndefined()
  })

  it("leaves the pass to the core while the table is not known, so that a layout that did not load can be mended by it", async () => {
    vi.mocked(calls.fetchComposition).mockResolvedValue({ ok: false, text: "x" } as Answer<Composition>)
    const w = mountOutput()
    await flushPromises()
    expect(w.find(".output-sync").attributes("disabled")).toBeUndefined()
  })

  it("shows how far the reads of the pages the pass waits for are, by site, and drops each when its wait ends", async () => {
    const reads = useSeriesReadsStore()
    const w = mountOutput()
    await flushPromises()
    reads.apply({ series_id: 1, source_id: 7, url: "https://1.ruchime.org/release/x", phase: "episodes", current: 34,
      total: 391, rate: 0.6 })
    await flushPromises()
    expect(w.findAll(".output-reading").map((line) => line.text())).toEqual([
      t("output.reading", { site: "1.ruchime.org", text: "Страницы серий: 34 из 391 | 0.6 с/с" })])
    reads.apply({ series_id: 1, source_id: 7, url: "https://1.ruchime.org/release/x", active: false })
    await flushPromises()
    expect(w.findAll(".output-reading")).toHaveLength(0)
  })

  it("names the sources of the series by their place and colour", async () => {
    useSeriesStore().upsert({ id: 1, name: "Тайтл", sources: [
      { id: 8, series_id: 1, position: 1, url: "https://vkvideo.ru/@chan|x", source_type: "vk_video", color: 3 },
      { id: 7, series_id: 1, position: 0, url: "https://1.ruchime.org/release/x", source_type: "web_video", color: 0 },
    ] } as never)
    const w = mountOutput()
    await flushPromises()
    expect(w.findAll(".output-source").map((each) => each.text())).toEqual(["11.ruchime.org", "2vkvideo.ru @chan"])
    expect(w.find(".episode-card .figure-mark").text()).toBe("1")
  })
})
