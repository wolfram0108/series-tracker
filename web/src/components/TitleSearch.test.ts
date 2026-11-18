// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest"
import { flushPromises, mount } from "@vue/test-utils"
import ToastService from "primevue/toastservice"
import SourcesEditor from "./SourcesEditor.vue"
import * as address from "../composables/sourceAddress"
import * as calls from "../composables/sourceCalls"
import { t } from "../i18n"
import { itemOfSource, type SeriesSource, type TitleCard, type TitleSearchReply } from "../lib/sources"

vi.mock("../composables/sourceAddress")
vi.mock("../composables/sourceCalls", async (original) => ({
  ...(await original<typeof import("../composables/sourceCalls")>()),
  searchTitles: vi.fn(),
}))

const card = (over: Partial<TitleCard>): TitleCard => ({
  id: "", name: "万界独尊", sub: "", year: "", area: "", genres: "", remarks: "", blurb: "", pic: "", lines: [], ...over,
})
const page = (titles: TitleCard[], over: Partial<TitleSearchReply> = {}): TitleSearchReply => ({
  titles, page: 1, pages: 2, total: 3, notes: [], reason: null, ...over,
})
const stored = (over: Partial<SeriesSource> = {}): SeriesSource => ({
  id: 7, series_id: 1, position: 0, url: "title://chines/k3f9a1/CO4K", source_type: "web_video", parser_profile_id: null,
  vibix_season: null, players_off: [], rows_per_line: "all", color: 0, ...over,
})

beforeEach(() => {
  vi.mocked(address.kindOf).mockReset().mockResolvedValue({ kind: "site", sourceType: "web_video" })
  vi.mocked(calls.searchTitles).mockReset()
})

const mountEditor = (props: Record<string, unknown> = {}) => mount(SourcesEditor, {
  props: {
    items: [itemOfSource(stored(), null, null)], profiles: [], adder: vi.fn().mockResolvedValue(true),
    names: [{ text: "万界独尊", mark: "TMDB" }, { text: "Ten Thousand Worlds" }], ...props,
  },
  global: { plugins: [ToastService] },
})

describe("the search of the titles of the provider in the sources", () => {
  it("is a mode beside the address; the field starts with the first name and every name is a button that puts it there", async () => {
    const w = mountEditor()
    expect(w.find(".source-add").exists()).toBe(true)
    await w.findAll(".source-mode .st-tab")[1].trigger("click")
    expect(w.find(".source-add").exists()).toBe(false)
    expect((w.find(".ts-query input").element as HTMLInputElement).value).toBe("万界独尊")
    expect(w.findAll(".ts-name").map((each) => each.text())).toEqual(["万界独尊TMDB", "Ten Thousand Worlds"])
    await w.findAll(".ts-name")[1].trigger("click")
    expect((w.find(".ts-query input").element as HTMLInputElement).value).toBe("Ten Thousand Worlds")
  })

  it("shows every title the provider gave with what it says; each line of it has its own button and a line already a source is marked by its place", async () => {
    vi.mocked(calls.searchTitles).mockResolvedValue({ ok: true, data: page([
      card({ id: "819312", sub: "我身为剑", year: "2013", area: "内地", genres: "动画,国漫", blurb: "《我是大医生》", pic: "https://img.model/1.jpg",
             lines: [{ code: "CO4K", url: "title://chines/819312/CO4K", label: "4K", rows: 480, routes: 3 },
                     { code: "duanju", url: "title://chines/819312/duanju", label: "", rows: null, routes: 3 }] }),
      card({ id: "k3f9a1", sub: "Ten Thousand Worlds", remarks: "更新至第499集",
             lines: [{ code: "CO4K", url: "title://chines/k3f9a1/CO4K", label: "4K", rows: null, routes: 3 }] }),
    ]) })
    const adder = vi.fn().mockResolvedValue(true)
    const w = mountEditor({ adder })
    await w.findAll(".source-mode .st-tab")[1].trigger("click")
    await w.find(".ts-find").trigger("click")
    await flushPromises()

    expect(calls.searchTitles).toHaveBeenCalledWith("万界独尊", 1)
    expect(w.find(".ts-head").text()).toBe(t("sources.titles.found", { total: 3, page: 1, pages: 2 }))
    const cards = w.findAll(".ts-card")
    expect(cards.map((each) => each.attributes("data-card"))).toEqual(["819312", "k3f9a1"])
    expect(cards[0].find(".ts-meta").text()).toBe("2013 · 内地 · 动画, 国漫")
    expect(cards[0].find(".ts-blurb").text()).toBe("《我是大医生》")
    expect(cards[0].find("img").attributes("src")).toBe("https://img.model/1.jpg")
    expect(cards[0].findAll(".ts-line").map((each) => each.find(".ts-chip").text())).toEqual(["4K · 480", "duanju"])
    expect(cards[0].findAll(".ts-add")).toHaveLength(2)  // a title is no source: each of its lines is
    expect(cards[1].find(".ts-remarks").text()).toBe("更新至第499集")
    // the line of the second title is the source the series already has
    // (stored: title://chines/k3f9a1/CO4K)

    await cards[0].find(".ts-line[data-line='duanju'] .ts-add").trigger("click")
    expect(adder).toHaveBeenCalledWith("title://chines/819312/duanju")

    vi.mocked(calls.searchTitles).mockResolvedValue({ ok: true, data: page([card({ id: "5", lines: [{ code: "X", url: "title://chines/5/X", label: "", rows: null, routes: 1 }] })], { page: 2 }) })
    await w.find(".ts-more").trigger("click")
    await flushPromises()
    expect(calls.searchTitles).toHaveBeenLastCalledWith("万界独尊", 2)
    expect(w.findAll(".ts-card").map((each) => each.attributes("data-card"))).toEqual(["819312", "k3f9a1", "5"])
    expect(w.find(".ts-more").exists()).toBe(false)
  })

  it("marks the line that is already a source of the series by its place and gives it no button", async () => {
    vi.mocked(calls.searchTitles).mockResolvedValue({ ok: true, data: page([
      card({ id: "k3f9a1", lines: [{ code: "CO4K", url: "title://chines/k3f9a1/CO4K", label: "4K", rows: null, routes: 3 },
                                    { code: "duanju", url: "title://chines/k3f9a1/duanju", label: "", rows: null, routes: 3 }] }),
    ]) })
    const w = mountEditor({ items: [itemOfSource(stored({ url: "title://chines/k3f9a1/CO4K" }), null, null)] })
    await w.findAll(".source-mode .st-tab")[1].trigger("click")
    await w.find(".ts-find").trigger("click")
    await flushPromises()
    const done = w.find(".ts-line[data-line='CO4K']")
    expect(done.classes()).toContain("is-added")
    expect(done.find(".ts-added").text()).toBe(t("sources.titles.added", { n: 1 }))
    expect(done.find(".ts-add").exists()).toBe(false)
    expect(w.find(".ts-line[data-line='duanju'] .ts-add").exists()).toBe(true)
  })

  it("a provider that gave no title says why, worded from its reason, and what a part of its sources lacked from its notes", async () => {
    vi.mocked(calls.searchTitles).mockResolvedValue({ ok: true, data: page([], {
      reason: JSON.stringify({ code: "search_unavailable" }), notes: [JSON.stringify({ code: "search_cooling", wait: 30 })] }) })
    const w = mountEditor()
    await w.findAll(".source-mode .st-tab")[1].trigger("click")
    await w.find(".ts-find").trigger("click")
    await flushPromises()
    const said = w.findAll(".ts-reason").map((each) => each.text())
    expect(said).toHaveLength(2)
    expect(said[0]).toBe(t("reason.search_unavailable"))
    expect(w.find(".ts-more").exists()).toBe(false)
  })

  it("a source of the provider is headed by the name its site gives it; its field is its address, with no page to open", async () => {
    const summary = { source_id: 7, url: "title://chines/k3f9a1/CO4K", status: "no_profile" as const, title: "万界独尊 (2021)", players: [] }
    const w = mountEditor({ items: [itemOfSource(stored(), summary, null)] })
    await flushPromises()
    expect(w.find(".source-place").text()).toBe("万界独尊 (2021)")
    expect(w.find(".source-title .item-value").text()).toBe("title://chines/k3f9a1/CO4K")
    expect(w.find(".source-title a").exists()).toBe(false)
  })
})
