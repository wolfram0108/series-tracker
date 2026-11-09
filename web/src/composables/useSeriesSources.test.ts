// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest"
import { defineComponent, h } from "vue"
import { flushPromises, mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { useSeriesSources } from "./useSeriesSources"
import * as calls from "./sourceCalls"
import { useCompositionStore } from "../stores/composition"
import { useSourceTablesStore } from "../stores/sourceTables"
import type { SeriesSource, SourceSummary } from "../lib/sources"

vi.mock("./sourceCalls")

const PAGE = "https://1.ruchime.org/release/test"
const source = (over: Partial<SeriesSource> = {}): SeriesSource => ({
  id: 7, series_id: 1, position: 0, url: PAGE, source_type: "web_video", parser_profile_id: null,
  vibix_season: null, players_off: [], color: 0, markup: false, ...over,
})
const summary: SourceSummary = {
  source_id: 7, url: PAGE, status: "no_profile",
  players: [{ player: "vk", player_label: "VK", rows: 3, groups: 0 }],
}
const ok = <T>(data: T) => ({ ok: true as const, data })

function setup(kind: "torrent" | "video" = "video", initial: SeriesSource[] = [source()]) {
  let sources!: ReturnType<typeof useSeriesSources>
  mount(defineComponent({ setup() { sources = useSeriesSources(1, kind, initial); return () => h("div") } }))
  return sources
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.mocked(calls.fetchSources).mockReset().mockResolvedValue(ok([source()]))
  vi.mocked(calls.fetchLayout).mockReset().mockResolvedValue(ok({ sources: [summary], rows: [] }))
  vi.mocked(calls.resolveAddress).mockReset().mockResolvedValue(ok({ site: "ruchime", canonical_url: PAGE }))
  vi.mocked(calls.createSource).mockReset().mockResolvedValue(ok({ id: 8 }))
  vi.mocked(calls.changeSource).mockReset().mockResolvedValue(ok({ success: true }))
  vi.mocked(calls.removeSource).mockReset().mockResolvedValue(ok({ success: true }))
})

describe("useSeriesSources", () => {
  it("reads the sources and their summaries when the window opens and follows the page by its canonical address", async () => {
    const sources = setup()
    await flushPromises()
    expect(sources.items.value[0].view.players.map((p) => p.title)).toEqual(["VK"])
    expect(sources.items.value[0].view.state).toBe("noProfile")
    useSourceTablesStore().applyProgress({ url: PAGE, phase: "episodes", current: 2, total: 9 })
    expect(sources.items.value[0].view).toMatchObject({ state: "reading", progress: { current: 2, total: 9 } })
  })

  it("keeps the newer reading when an older one answers late", async () => {
    let release!: (value: ReturnType<typeof ok<SeriesSource[]>>) => void
    vi.mocked(calls.fetchSources).mockReset()
      .mockImplementationOnce(() => new Promise((resolve) => { release = resolve }))
      .mockResolvedValue(ok([source()]))
    const sources = setup()
    await flushPromises()
    useCompositionStore().applyChanged({ series_id: 1, rev: 5, episodes: [], summary_only: true })
    await flushPromises()
    release(ok([source(), source({ id: 9, position: 1, url: "https://1.ruchime.org/release/old" })]))
    await flushPromises()
    expect(sources.items.value).toHaveLength(1)
  })

  it("reads no layout for a torrent series: it has no table", async () => {
    setup("torrent", [source({ source_type: "torrent", url: "https://kinozal.tv/details.php?id=1" })])
    await flushPromises()
    expect(calls.fetchSources).toHaveBeenCalledTimes(1)
    expect(calls.fetchLayout).not.toHaveBeenCalled()
  })

  it("reads the sources again when the layout of the series changes, and not for another series", async () => {
    setup()
    await flushPromises()
    const before = vi.mocked(calls.fetchSources).mock.calls.length
    const store = useCompositionStore()
    store.applyChanged({ series_id: 2, rev: 1, episodes: [], summary_only: false })
    await flushPromises()
    expect(calls.fetchSources).toHaveBeenCalledTimes(before)
    store.applyChanged({ series_id: 1, rev: 4, episodes: [], summary_only: true })
    await flushPromises()
    expect(calls.fetchSources).toHaveBeenCalledTimes(before + 1)
  })

  it("reads the page of a source now and says why when the core refuses", async () => {
    vi.mocked(calls.readPageNow).mockReset().mockResolvedValue(ok({ success: true }))
    const channel = source({ source_type: "vk_video", url: "https://vk.com/video/@c|Тайтл" })
    vi.mocked(calls.fetchSources).mockResolvedValue(ok([channel]))
    const sources = setup("video", [channel])
    await flushPromises()
    await sources.reread(7)
    expect(calls.readPageNow).toHaveBeenCalledWith("https://vk.com/video/@c|Тайтл")
    vi.mocked(calls.readPageNow).mockResolvedValueOnce({ ok: false, text: "Нет связи с сервером" })
    await sources.reread(7)
    expect(sources.error.value).toBe("Нет связи с сервером")
    await sources.reread(99)
    expect(calls.readPageNow).toHaveBeenCalledTimes(2)
  })

  it("words a recount of the layout that failed for the series, and for no other, until the next layout comes", async () => {
    const sources = setup()
    await flushPromises()
    expect(sources.layoutFailure.value).toBe("")
    const store = useCompositionStore()
    store.applyFailed({ series_id: 2, reason: "{}" })
    expect(sources.layoutFailure.value).toBe("")
    store.applyFailed({ series_id: 1, reason: JSON.stringify({ code: "composition_error" }) })
    expect(sources.layoutFailure.value).toContain("Раскладка не пересчитана, действует прежняя")
    store.applyChanged({ series_id: 1, rev: 3, episodes: [], summary_only: false })
    expect(sources.layoutFailure.value).toBe("")
  })

  it("adds a source made of a site address and reads the list again", async () => {
    const sources = setup()
    await flushPromises()
    expect(await sources.add(`${PAGE}?x=1`)).toBe(true)
    expect(calls.createSource).toHaveBeenCalledWith(1, expect.objectContaining({ url: PAGE, source_type: "web_video", parser_profile_id: null }))
    expect(calls.fetchSources).toHaveBeenCalledTimes(2)
  })

  it("refuses an address that no site reads, in words, and sends nothing", async () => {
    vi.mocked(calls.resolveAddress).mockResolvedValue(ok({ site: null, canonical_url: null }))
    const sources = setup()
    await flushPromises()
    expect(await sources.add("https://kinozal.tv/details.php?id=1")).toBe(false)
    expect(sources.error.value).toBe("Этот адрес не читает ни один сайт")
    expect(calls.createSource).not.toHaveBeenCalled()
  })

  it("shows the refusal to remove the last source and keeps the source", async () => {
    vi.mocked(calls.removeSource).mockResolvedValue({ ok: false, text: "Единственный источник сериала удалить нельзя" })
    const sources = setup()
    await flushPromises()
    await sources.remove(7)
    expect(sources.error.value).toBe("Единственный источник сериала удалить нельзя")
    expect(sources.items.value).toHaveLength(1)
    expect(calls.fetchSources).toHaveBeenCalledTimes(1)
  })

  it("sends a choice at once with the names the core knows and shows it without waiting for an event", async () => {
    const sources = setup()
    await flushPromises()
    await sources.change(7, { profileId: 4, playersOff: ["vk"], vibixSeason: 2 })
    expect(calls.changeSource).toHaveBeenCalledWith(1, 7, { parser_profile_id: 4, players_off: ["vk"], vibix_season: 2 })
    expect(sources.items.value[0]).toMatchObject({ profileId: 4, playersOff: ["vk"], vibixSeason: 2 })
    expect(sources.items.value[0].view.players[0].off).toBe(true)
  })

  it("keeps the old choice when the core refuses the new one", async () => {
    vi.mocked(calls.changeSource).mockResolvedValue({ ok: false, text: "Профиль правил не найден" })
    const sources = setup()
    await flushPromises()
    await sources.change(7, { profileId: 99 })
    expect(sources.items.value[0].profileId).toBeNull()
    expect(sources.error.value).toBe("Профиль правил не найден")
  })
})
