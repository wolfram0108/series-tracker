// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest"
import { flushPromises, mount } from "@vue/test-utils"
import ToastService from "primevue/toastservice"
import SourcesEditor from "./SourcesEditor.vue"
import * as address from "../composables/sourceAddress"
import { t } from "../i18n"
import { itemOfSource, type LayoutRow, type SeriesSource, type SourceSummary } from "../lib/sources"

vi.mock("../composables/sourceAddress")

const PAGE = "https://1.ruchime.org/release/test"
const stored = (over: Partial<SeriesSource> = {}): SeriesSource => ({
  id: 7, series_id: 1, position: 0, url: PAGE, source_type: "web_video", parser_profile_id: null,
  vibix_season: null, players_off: [], color: 0, markup: false, ...over,
})
const summary: SourceSummary = {
  source_id: 7, url: PAGE, status: "no_profile", read_at: "2026-11-01T10:00:00Z", players: [
    { player: "vk", player_label: "VK", rows: 13, groups: 0 },
    { player: "vibix", player_label: "Vibix", rows: 23, groups: 2 },
    { player: "kodik", player_label: "", rows: 5, groups: 1 },
  ],
}
const mountEditor = (items = [itemOfSource(stored(), summary, null)], props: Record<string, unknown> = {}) =>
  mount(SourcesEditor, {
    props: { items, profiles: [{ id: 4, name: "ruchime" }], adder: vi.fn().mockResolvedValue(true), ...props },
    global: { plugins: [ToastService] },
  })

// What the core says of a typed address: a channel of VK for the addresses of vkvideo.ru, a page of a site for the rest.
beforeEach(() => {
  vi.mocked(address.kindOf).mockReset().mockImplementation(async (typed: string) =>
    ({ kind: "site", sourceType: typed.includes("vkvideo.ru/@") ? "vk_video" : "web_video" }))
})

describe("SourcesEditor", () => {
  it("names a source by its place in the head, and draws its players as tabs with their counts and no table without rows", () => {
    const w = mountEditor()
    expect(w.find(".source-head .source-place").text()).toBe("1.ruchime.org")
    expect(w.find(".source-head").text()).toContain("3 плеера, 41 строка")
    expect(w.findAll(".player-tab").map((tab) => [tab.find(".player-name").text(), tab.find(".player-rows").text()]))
      .toEqual([["VK", "13"], ["Vibix", "23"], ["kodik", "5"]])
    for (const word of ["сезона", "до 1080p", "группа"]) expect(w.text()).not.toContain(word)
    expect(w.find(".voice-table").exists()).toBe(false)
  })

  it("names a channel of VK by its site and its channel, as the output tab does", () => {
    const channel = itemOfSource(stored({ source_type: "vk_video", url: "https://vkvideo.ru/@kino|тайтл" }), null, null)
    expect(mountEditor([channel]).find(".source-place").text()).toBe("vkvideo.ru @kino")
  })

  it("draws the episodes with their gaps and a row per voice-over with its group, from the rows of the constructor only", async () => {
    const part = (over: Partial<LayoutRow>): LayoutRow => ({
      series_id: 1, source_id: 7, row_key: "r", excluded: false, season: 1, episode_start: 1, episode_end: 1,
      voice_label: "", voice_name: "", voice_group: null, player: "vk", player_label: "VK", group: "", text: "",
      heights: [], state: "playable", link: "", video: "", ...over,
    })
    const rows = [
      part({ row_key: "a", episode_start: 1, episode_end: 10, heights: [480, 2160], voice_name: "ПВА ШОУ", voice_label: "ПВА ШОУ", voice_group: "ПВА ШОУ" }),
      part({ row_key: "b", episode_start: 11, episode_end: 50, heights: [480, 720], voice_label: "Неизвестная озвучка 2" }),
      part({ row_key: "c", episode_start: 61, episode_end: 120, heights: [360, 1440], voice_label: "Неизвестная озвучка 2" }),
    ]
    const w = mountEditor([itemOfSource(stored({ parser_profile_id: 4 }), { ...summary, status: "ready" }, null, rows)])
    expect(w.find(".player-fact b").text()).toBe("1–120")
    expect(w.find(".player-fact .player-gaps").text()).toBe("пропуски: 51–60")
    expect(w.find(".player-top").text()).toContain("Разрешение 360p–2160p")
    expect(w.findAll(".voice-table tbody tr").map((row) => row.findAll("td").map((cell) => cell.text()))).toEqual([
      ["ПВА ШОУ", "ПВА ШОУ", "1–10", "480p–2160p", "1"],
      ["Нет", "Неизвестная озвучка 2", "11–120 пропуски: 51–60", "11–50: 480p–720p61–120: 360p–1440p", "2"],
    ])
    await w.findAll(".player-tab")[1].trigger("click")
    expect(w.find(".voice-table").exists()).toBe(false)
    expect(w.find(".player").text()).toContain("Правила профиля не определили ни одной серии")
  })

  it("offers the season of the catalog only at the player that keeps several", async () => {
    const w = mountEditor()
    const offered: string[] = []
    for (const tab of w.findAll(".player-tab")) {
      await tab.trigger("click")
      if (w.find(".player-season").exists()) offered.push(w.find(".player").attributes("data-player") as string)
    }
    expect(offered).toEqual(["vibix"])
  })

  it("tells the profile is missing and that rows have no numbers without it", () => {
    const text = mountEditor().text()
    expect(text).toContain("Профиль конструктора")
    expect(text).toContain("Пока профиль не назначен, у строк нет номеров серий.")
  })

  it("switches a player off with the list of the disabled ones", async () => {
    const w = mountEditor([itemOfSource(stored({ players_off: ["kodik"] }), summary, null)])
    expect(w.findAll(".player-tab").map((tab) => tab.classes().includes("is-off"))).toEqual([false, false, true])
    await w.findAll(".player-tab")[2].trigger("click")
    expect((w.find(".player-switch input").element as HTMLInputElement).checked).toBe(false)
    await w.findAll(".player-tab")[0].trigger("click")
    await w.find(".player-switch input").setValue(false)
    expect(w.emitted("change")?.[0]).toEqual([7, { playersOff: ["kodik", "vk"] }])
  })

  it("marks a stored source the markup reference, and has no such switch for a draft", async () => {
    const w = mountEditor([itemOfSource(stored(), summary, null)])
    await w.find(".markup-switch input").setValue(true)
    expect(w.emitted("change")?.[0]).toEqual([7, { markup: true }])
    const marked = mountEditor([itemOfSource(stored({ markup: true }), summary, null)])
    expect((marked.find(".markup-switch input").element as HTMLInputElement).checked).toBe(true)
    const draft = mountEditor([{ ...itemOfSource(stored(), summary, null), markup: null }])
    expect(draft.find(".markup-switch").exists()).toBe(false)
  })

  it("asks to remove a source by its id", async () => {
    const w = mountEditor()
    await w.find("button.source-remove").trigger("click")
    expect(w.emitted("remove")?.[0]).toEqual([7])
  })

  it("asks to read a source again by its id, and not while it is being read", async () => {
    const w = mountEditor()
    await w.find("button.source-reread").trigger("click")
    expect(w.emitted("reread")?.[0]).toEqual([7])
    const reading = itemOfSource(stored(), summary, { state: "reading", progress: null, reason: null, readAt: null })
    expect(mountEditor([reading]).find("button.source-reread").attributes("disabled")).toBeDefined()
  })

  it("shows the progress of a read and the words of a failure", () => {
    const reading = itemOfSource(stored(), summary, {
      state: "reading", progress: { phase: "episodes", current: 3, total: 20, rate: null }, reason: null, readAt: null,
    })
    expect(mountEditor([reading]).text()).toContain("Страницы серий: 3 из 20")
    const failed = itemOfSource(stored(), summary, { state: "failed", progress: null, reason: '{"code":"read_failed"}', readAt: null })
    const text = mountEditor([failed]).text()
    expect(text).toContain("Чтение не удалось")
    expect(text).toContain("чтение страницы не удалось")
  })

  it("keeps the address of a torrent source and offers neither adding nor removing", () => {
    const torrent = itemOfSource(stored({ source_type: "torrent", url: "https://kinozal.tv/details.php?id=1" }), null, null)
    const w = mountEditor([torrent], { editable: false })
    expect(w.find("button.source-remove").exists()).toBe(false)
    expect(w.find(".source-add").exists()).toBe(false)
    expect((w.find("input[readonly]").element as HTMLInputElement).value).toBe("https://kinozal.tv/details.php?id=1")
    expect(w.find(".players").exists()).toBe(false)
  })

  it("hands a typed address to the host and clears the field only when a source was made of it", async () => {
    const adder = vi.fn().mockResolvedValueOnce(false).mockResolvedValueOnce(true)
    const w = mountEditor([], { adder })
    const field = w.find(".source-add input")
    await field.setValue(`  ${PAGE}  `)
    await w.find(".source-add button").trigger("click")
    await flushPromises()
    expect(adder).toHaveBeenLastCalledWith(PAGE)
    expect((field.element as HTMLInputElement).value).toBe(`  ${PAGE}  `)
    await w.find(".source-add button").trigger("click")
    await flushPromises()
    expect((field.element as HTMLInputElement).value).toBe("")
  })

  it("offers no way to make a group of voice-overs: the groups are made in the settings only", () => {
    const w = mountEditor()
    expect(w.find(".group-maker").exists()).toBe(false)
    expect(w.text()).not.toContain("Завести группу")
    expect(w.text()).not.toContain("Название группы")
  })

  it("offers the field of the query as soon as the address is a channel of VK and adds the channel with it only", async () => {
    const adder = vi.fn().mockResolvedValue(true)
    const w = mountEditor([], { adder })
    expect(w.find(".source-query").exists()).toBe(false)
    await w.find(".source-add input").setValue("https://vkvideo.ru/@kino")
    await flushPromises()
    const query = w.find(".source-query input")
    const button = w.find(".source-add button")
    expect(query.exists()).toBe(true)
    expect(w.text()).toContain(t("sources.vk.hint"))
    expect(button.attributes("disabled")).toBeDefined()
    await w.find(".source-add input").trigger("keyup.enter")
    await flushPromises()
    expect(adder).not.toHaveBeenCalled()
    await query.setValue(" тайтл ")
    expect(button.attributes("disabled")).toBeUndefined()
    await button.trigger("click")
    await flushPromises()
    expect(adder).toHaveBeenCalledWith("https://vkvideo.ru/@kino|тайтл")
    expect((w.find(".source-add input").element as HTMLInputElement).value).toBe("")
    expect(w.find(".source-query").exists()).toBe(false)
  })

  it("parts a pasted channel with its query into the two fields", async () => {
    const w = mountEditor([])
    await w.find(".source-add input").setValue("https://vkvideo.ru/@kino|Боевой мастер")
    await flushPromises()
    expect((w.find(".source-add input").element as HTMLInputElement).value).toBe("https://vkvideo.ru/@kino")
    expect((w.find(".source-query input").element as HTMLInputElement).value).toBe("Боевой мастер")
  })

  it("keeps the field of the query away from the pages of sites", async () => {
    const w = mountEditor([])
    await w.find(".source-add input").setValue(PAGE)
    await flushPromises()
    expect(w.find(".source-query").exists()).toBe(false)
    expect(w.find(".source-add button").attributes("disabled")).toBeUndefined()
  })

  it("does not send a channel of VK whose query was emptied, and says that it needs one", async () => {
    const channel = itemOfSource(stored({ source_type: "vk_video", url: "https://vkvideo.ru/@kino|тайтл" }), null, null)
    const w = mountEditor([channel])
    const fields = w.findAll(".grid-fit input")
    await fields[1].setValue("  ")
    await fields[1].trigger("focusout")
    expect(w.emitted("change")).toBeUndefined()
    expect(w.text()).toContain(t("sources.vk.queryMissing"))
  })

  it("edits the channel of VK and sends it when the field is left", async () => {
    const channel = itemOfSource(stored({ source_type: "vk_video", url: "https://vkvideo.ru/@kino|тайтл" }), null, null)
    const w = mountEditor([channel])
    const fields = w.findAll(".grid-fit input")
    expect((fields[0].element as HTMLInputElement).value).toBe("https://vkvideo.ru/@kino")
    expect((fields[1].element as HTMLInputElement).value).toBe("тайтл")
    await fields[1].setValue("второй")
    expect(w.emitted("change")).toBeUndefined()
    await fields[1].trigger("focusout")
    expect(w.emitted("change")?.[0]).toEqual([7, { url: "https://vkvideo.ru/@kino|второй" }])
  })
})
