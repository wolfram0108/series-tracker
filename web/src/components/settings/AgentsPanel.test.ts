// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { flushPromises, mount, type VueWrapper } from "@vue/test-utils"
import ToastService from "primevue/toastservice"
import { createPinia, setActivePinia } from "pinia"
import { nextTick } from "vue"

const client = vi.hoisted(() => ({ GET: vi.fn(), POST: vi.fn(), PUT: vi.fn(), DELETE: vi.fn() }))
vi.mock("../../api/client", () => ({ api: client }))
vi.mock("../../composables/sourceCalls", () => ({ stopReading: vi.fn().mockResolvedValue({ ok: true, data: {} }) }))
// The queues are brought by the stream: whether it is up decides whether an empty queue is told as empty.
const sse = vi.hoisted(() => ({ live: null as null | { value: boolean } }))
vi.mock("../../composables/useSSE", async () => {
  const { ref } = await import("vue")
  sse.live = ref(true)
  return { useSSE: () => ({ connected: sse.live }) }
})
import { stopReading } from "../../composables/sourceCalls"
import AgentsPanel from "./AgentsPanel.vue"
import { useQueuesStore, type ConformTask, type DownloadCard, type PackCard } from "../../stores/queues"
import { useSeriesReadsStore } from "../../stores/seriesReads"
import { useSeriesStore, type Series } from "../../stores/series"
import { t } from "../../i18n"

let mounted: VueWrapper | null = null
beforeEach(() => {
  setActivePinia(createPinia())
  sse.live!.value = true
  client.DELETE.mockReset().mockResolvedValue({ data: { success: true } })
})
afterEach(() => mounted?.unmount())

const download = (seriesId: number, episode: string, status = "download"): DownloadCard => ({
  id: `${seriesId}:${episode}:dub:A`,
  need: { key: `${seriesId}:${episode}:dub:A`, series_id: seriesId, series_name: "", sync_started: "", episode, kind: "dub", group: "A", voice_number: 1, cap: null },
  variant: null, status, method: "", stages: [], log: [], figures: {}, replanned: false, position: null, running: 0, limit: 0,
  queued_at: "", finished_at: "", seconds: 0,
} as unknown as DownloadCard)
const pack = (seriesId: number): PackCard => ({
  id: `${seriesId}:s01e01`, series_id: seriesId, series_name: "", sync_started: "", episode: "s01e01", name: "e1.mkv", video: null, status: "build",
  stages: [], tracks: [], reason: "", waited: false, failure: "", figures: {}, queued_at: "", finished_at: "", seconds: 0,
} as unknown as PackCard)
const job = (id: string, seriesId: number | null): ConformTask => ({
  id, label: `Show s01e01`, key: null, series_id: seriesId, status: "queued", progress: 0, stage: "", stage_pct: 0, detail: "", dub_index: 0, dub_total: 1,
  cur_dub: "", dubs: ["a.mp4"], elapsed_s: 0, created_at: "2026-11-13T08:44:00+00:00", error: null, results: [],
})

function fill() {
  const series = useSeriesStore()
  series.list = [{ id: 46, name: "Повелитель" }, { id: 50, name: "Братик" }] as Series[]
  const queues = useQueuesStore()
  queues.setDownloads([download(46, "s01e30"), download(46, "s01e31", "queued"), download(50, "s01e12", "failed")])
  queues.setPack([pack(50)])
  queues.setConform({ daemon: { up: true, version: "0.14.2" }, tasks: [job("c1", 46), job("c2", null)] })
  return queues
}
const mountPanel = (props: Record<string, unknown> = {}) => {
  mounted = mount(AgentsPanel, { props, global: { plugins: [ToastService], stubs: { ConformPlotViewer: true } } })
  return mounted
}
const sectionOf = (w: VueWrapper, key: string) => w.find(`[data-section="${key}"]`)
const seriesOfCards = (w: VueWrapper, key: string) => sectionOf(w, key).findAll(".work-card").map((card) => card.attributes("data-series") ?? null)

describe("the agents", () => {
  it("lists the work of every agent as cards of one kind, each of its series, and says when an agent has none", () => {
    fill()
    const w = mountPanel()
    expect(seriesOfCards(w, "download")).toEqual(["46", "46", "50"])
    expect(seriesOfCards(w, "pack")).toEqual(["50"])
    expect(seriesOfCards(w, "conform")).toEqual(["46", null])
    expect(sectionOf(w, "read").text()).toContain(t("agents.empty"))
    expect(sectionOf(w, "conform").text()).toContain(t("agents.conform.daemon.up"))
    expect(w.find(".work-card .work-series").text()).toBe("#46")
    expect(w.find(".work-card").text()).toContain("Повелитель")
  })

  it("narrows every agent to the series picked, and counts what is left", async () => {
    fill()
    const w = mountPanel()
    await w.find(".item-select").trigger("click")
    const options = [...document.body.querySelectorAll(".options-list .option")].map((each) => each.textContent)
    expect(options).toEqual([t("agents.filter.all"), "#46 Повелитель", "#50 Братик"])
    ;(document.body.querySelectorAll(".options-list .option")[2] as HTMLElement).click()
    await nextTick()
    expect(seriesOfCards(w, "download")).toEqual(["50"])
    expect(seriesOfCards(w, "conform")).toEqual([])
    expect(w.findAll(".agents-chip").map((chip) => chip.text().replace(/\s+/g, " "))).toContain(`${t("state.download")} 1`.replace(/^./, (c) => c.toUpperCase()))
  })

  it("in the window of a series is that series alone, with no choice of another", () => {
    fill()
    const w = mountPanel({ seriesId: 46 })
    expect(w.find(".item-select").exists()).toBe(false)
    expect(w.find(".agents-fixed").text()).toBe("#46 Повелитель")
    expect(seriesOfCards(w, "download")).toEqual(["46", "46"])
    expect(seriesOfCards(w, "pack")).toEqual([])
  })

  it("removes a card of a download that ended, and cancels a job of the alignment, by their ids", async () => {
    fill()
    const w = mountPanel()
    await sectionOf(w, "download").findAll(".work-card")[2].find(".work-action").trigger("click")
    await flushPromises()
    expect(client.DELETE).toHaveBeenCalledWith("/api/downloads/cards/{card_id}", { params: { path: { card_id: "50:s01e12:dub:A" } } })
    await sectionOf(w, "conform").findAll(".work-card")[0].find(".work-action").trigger("click")
    await flushPromises()
    expect(client.DELETE).toHaveBeenLastCalledWith("/api/conform/jobs/{job_id}", { params: { path: { job_id: "c1" } } })
  })

  it("shows the reads of the pages of the series and stops one by its address", async () => {
    const reads = useSeriesReadsStore()
    reads.apply({ series_id: 54, source_id: 8, url: "https://vkvideo.ru/@pvashow|тайтл", phase: "videos", current: 12, total: 40, rate: 3.1 })
    const w = mountPanel()
    const card = sectionOf(w, "read").find(".work-card")
    expect(card.attributes("data-series")).toBe("54")
    await card.find(".work-action").trigger("click")
    await flushPromises()
    expect(stopReading).toHaveBeenCalledWith("https://vkvideo.ru/@pvashow|тайтл")
  })

  it("says that the daemon is not there instead of listing its jobs", () => {
    const queues = useQueuesStore()
    queues.setConform({ daemon: { up: false, error: '{"code":"conform_down","error":"x"}' }, tasks: [] })
    const w = mountPanel()
    expect(sectionOf(w, "conform").find(".queue-empty").text()).toContain(t("agents.conform.daemon.down", { error: t("reason.conform_down", { error: "x" }) }))
  })

  it("does not say a queue is empty before the stream that brings the queues is up", async () => {
    sse.live!.value = false
    const w = mountPanel()
    expect(sectionOf(w, "read").find(".queue-empty").text()).toBe(t("common.fetching"))
    sse.live!.value = true
    await nextTick()
    expect(sectionOf(w, "read").find(".queue-empty").text()).toBe(t("agents.empty"))
  })
})
