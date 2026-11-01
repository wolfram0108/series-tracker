// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest"
import { flushPromises, mount } from "@vue/test-utils"
import ToastService from "primevue/toastservice"
import SourcesEditor from "./SourcesEditor.vue"
import { itemOfSource, type SeriesSource, type SourceSummary } from "../lib/sources"

const PAGE = "https://1.ruchime.org/release/test"
const stored = (over: Partial<SeriesSource> = {}): SeriesSource => ({
  id: 7, series_id: 1, position: 0, url: PAGE, source_type: "web_video", parser_profile_id: null,
  vk_search_mode: "search", vibix_season: null, players_off: [], ...over,
})
const summary: SourceSummary = {
  source_id: 7, url: PAGE, status: "no_profile", read_at: "2026-11-01T10:00:00Z", players: [
    { player: "vk", player_label: "VK", rows: 13, groups: 0, heights: [720, 1080], voices: [{ name: "Ruchime", group: "Ruchime", rows: 13 }] },
    { player: "vibix", player_label: "Vibix", rows: 23, groups: 2, heights: [1080], voices: [] },
    { player: "kodik", player_label: "", rows: 5, groups: 1, heights: [720], voices: [{ name: "Студия Константина Исаева", group: null, rows: 5 }] },
  ],
}
const mountEditor = (items = [itemOfSource(stored(), summary, null)], props: Record<string, unknown> = {}) =>
  mount(SourcesEditor, {
    props: { items, profiles: [{ id: 4, name: "ruchime" }], adder: vi.fn().mockResolvedValue(true), ...props },
    global: { plugins: [ToastService] },
  })

describe("SourcesEditor", () => {
  it("draws a group per source with its players, their counts and the voice-overs with their groups", () => {
    const text = mountEditor().text()
    expect(text).toContain("3 плеера, 41 строка")
    expect(text).toContain("13 строк")
    expect(text).toContain("23 строки, 2 сезона")
    expect(text).toContain("до 1080p")
    expect(text).toContain("группа «Ruchime»")
    expect(text).toContain("нет группы")
    expect(text).toContain("Студия Константина Исаева")
  })

  it("offers the season of the catalog only at the player that keeps several", () => {
    const w = mountEditor()
    expect(w.findAll("li.player")).toHaveLength(3)
    const withChoice = w.findAll("li.player").filter((li) => li.text().includes("Сезон каталога"))
    expect(withChoice).toHaveLength(1)
    expect(withChoice[0].text()).toContain("Vibix")
  })

  it("tells the profile is missing and that rows have no numbers without it", () => {
    const text = mountEditor().text()
    expect(text).toContain("Профиль конструктора")
    expect(text).toContain("Пока профиль не назначен, у строк нет номеров серий.")
  })

  it("switches a player off with the list of the disabled ones", async () => {
    const w = mountEditor([itemOfSource(stored({ players_off: ["kodik"] }), summary, null)])
    const boxes = w.findAll("li.player input[type=checkbox]")
    expect(boxes.map((box) => (box.element as HTMLInputElement).checked)).toEqual([true, true, false])
    await boxes[0].setValue(false)
    expect(w.emitted("change")?.[0]).toEqual([7, { playersOff: ["kodik", "vk"] }])
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
      state: "reading", progress: { phase: "episodes", current: 3, total: 20 }, reason: null, readAt: null,
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
    expect(w.findAll("li.player")).toHaveLength(0)
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
