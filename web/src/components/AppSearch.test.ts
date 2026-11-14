// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest"
import { flushPromises, mount } from "@vue/test-utils"
import ToastService from "primevue/toastservice"
import SourcesEditor from "./SourcesEditor.vue"
import * as address from "../composables/sourceAddress"
import * as calls from "../composables/sourceCalls"
import { t } from "../i18n"
import { itemOfSource, type AppCard, type AppResults, type AppSearchReply, type SeriesSource } from "../lib/sources"

vi.mock("../composables/sourceAddress")
vi.mock("../composables/sourceCalls", async (original) => ({
  ...(await original<typeof import("../composables/sourceCalls")>()),
  searchApps: vi.fn(),
}))

const card = (over: Partial<AppCard>): AppCard => ({
  app: "banri", id: "", url: "", name: "万界独尊", sub: "", year: "", area: "", genres: "", remarks: "", blurb: "", pic: "",
  ...over,
})
const page = (cards: AppCard[], over: Partial<AppResults> = {}): AppSearchReply => ({
  apps: [{ app: "banri", label: "半日", page: 1, pages: 2, total: 3, cards, reason: null, ...over }],
})
const stored = (over: Partial<SeriesSource> = {}): SeriesSource => ({
  id: 7, series_id: 1, position: 0, url: "app://banri/219017", source_type: "web_video", parser_profile_id: null,
  vibix_season: null, players_off: [], rows_per_line: "all", color: 0, ...over,
})

beforeEach(() => {
  vi.mocked(address.kindOf).mockReset().mockResolvedValue({ kind: "site", sourceType: "web_video" })
  vi.mocked(calls.searchApps).mockReset()
})

const mountEditor = (props: Record<string, unknown> = {}) => mount(SourcesEditor, {
  props: {
    items: [itemOfSource(stored(), null, null)], profiles: [], adder: vi.fn().mockResolvedValue(true),
    names: [{ text: "万界独尊", mark: "TMDB" }, { text: "Ten Thousand Worlds" }], ...props,
  },
  global: { plugins: [ToastService] },
})

describe("the search of the video applications in the sources", () => {
  it("is a mode beside the address; the field starts with the first name and every name is a button that puts it there", async () => {
    const w = mountEditor()
    expect(w.find(".source-add").exists()).toBe(true)
    await w.findAll(".source-mode .st-tab")[1].trigger("click")
    expect(w.find(".source-add").exists()).toBe(false)
    expect((w.find(".app-query input").element as HTMLInputElement).value).toBe("万界独尊")
    expect(w.findAll(".app-name").map((each) => each.text())).toEqual(["万界独尊TMDB", "Ten Thousand Worlds"])
    await w.findAll(".app-name")[1].trigger("click")
    expect((w.find(".app-query input").element as HTMLInputElement).value).toBe("Ten Thousand Worlds")
  })

  it("shows every card the application gave, in its order, with what it says; the card already a source is marked by its place", async () => {
    vi.mocked(calls.searchApps).mockResolvedValue({ ok: true, data: page([
      card({ id: "819312", url: "app://banri/819312", sub: "我身为剑", year: "2013", area: "内地", genres: "动画,国漫",
             blurb: "《我是大医生》", pic: "https://img.model/1.jpg" }),
      card({ id: "219017", url: "app://banri/219017", sub: "Ten Thousand Worlds", remarks: "更新至第499集" }),
      card({ id: "", url: "", name: "无号" }),
    ]) })
    const adder = vi.fn().mockResolvedValue(true)
    const w = mountEditor({ adder })
    await w.findAll(".source-mode .st-tab")[1].trigger("click")
    await w.find(".app-find").trigger("click")
    await flushPromises()

    expect(calls.searchApps).toHaveBeenCalledWith("万界独尊", 1)
    expect(w.find(".app-head").text()).toBe(`半日${t("sources.apps.found", { total: 3, page: 1, pages: 2 })}`)
    const cards = w.findAll(".app-card")
    expect(cards.map((each) => each.attributes("data-card"))).toEqual(["819312", "219017", ""])
    expect(cards[0].find(".app-meta").text()).toBe("2013 · 内地 · 动画, 国漫")
    expect(cards[0].find(".app-blurb").text()).toBe("《我是大医生》")
    expect(cards[0].find("img").attributes("src")).toBe("https://img.model/1.jpg")
    expect(cards[1].classes()).toContain("is-added")
    expect(cards[1].find(".app-added").text()).toBe(t("sources.apps.added", { n: 1 }))
    expect(cards[1].find(".app-remarks").text()).toBe("更新至第499集")
    expect(cards[2].text()).toContain(t("sources.apps.noAddress"))

    await cards[0].find(".app-add").trigger("click")
    expect(adder).toHaveBeenCalledWith("app://banri/819312")

    vi.mocked(calls.searchApps).mockResolvedValue({ ok: true, data: page([card({ id: "5", url: "app://banri/5" })], { page: 2 }) })
    await w.find(".app-more").trigger("click")
    await flushPromises()
    expect(calls.searchApps).toHaveBeenLastCalledWith("万界独尊", 2)
    expect(w.findAll(".app-card").map((each) => each.attributes("data-card"))).toEqual(["819312", "219017", "", "5"])
    expect(w.find(".app-more").exists()).toBe(false)
  })

  it("an application that gave no cards says why, worded from its reason", async () => {
    vi.mocked(calls.searchApps).mockResolvedValue({ ok: true, data: page([], {
      reason: JSON.stringify({ code: "app_unreachable", app: "半日", error: "ConnectTimeout" }) }) })
    const w = mountEditor()
    await w.findAll(".source-mode .st-tab")[1].trigger("click")
    await w.find(".app-find").trigger("click")
    await flushPromises()
    expect(w.find(".app-reason").text()).toBe(t("reason.app_unreachable", { app: "半日", error: "ConnectTimeout" }))
  })

  it("a source of an application is headed by the name its site gives it; its field is its address, with no page to open", async () => {
    const summary = { source_id: 7, url: "app://banri/219017", status: "no_profile" as const, title: "半日 · 万界独尊 (2021)", players: [] }
    const w = mountEditor({ items: [itemOfSource(stored(), summary, null)] })
    await flushPromises()
    expect(w.find(".source-place").text()).toBe("半日 · 万界独尊 (2021)")
    expect(w.find(".source-app-card .item-value").text()).toBe("app://banri/219017")
    expect(w.find(".source-app-card a").exists()).toBe(false)
  })
})
