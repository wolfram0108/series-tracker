// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest"
import { flushPromises, mount } from "@vue/test-utils"
import { ref } from "vue"
import PrimeVue from "primevue/config"
import ParserTest from "./ParserTest.vue"

const readPage = vi.fn()
vi.mock("../../composables/sourceCalls", async (original) => ({
  ...(await original<typeof import("../../composables/sourceCalls")>()),
  searchApps: vi.fn().mockResolvedValue({ ok: true, data: { apps: [{ app: "banri", label: "半日", page: 1, pages: 1, total: 1,
    reason: null, cards: [{ app: "banri", id: "219017", url: "app://banri/219017", name: "万界独尊", sub: "", year: "", area: "",
                            genres: "", remarks: "", blurb: "", pic: "" }] }] } }),
}))
vi.mock("../../composables/useRulesTest", () => ({
  useRulesTest: () => ({
    lines: ref([
      { key: "a", line: "Тайтл | VK | 2160p | Серия 1", result: null, heights: [1080, 2160] },
      { key: "b", line: "Тайтл | VK | Серия 2", result: null, heights: [] },
      { key: "c", line: "Серия 3, набранная в поле", result: null },
    ]),
    state: ref("ready"), reason: ref(null), error: ref(""), progress: ref(null), fetched: ref([]),
    readPage, runLines: vi.fn(), loadFilenames: vi.fn(),
  }),
}))

describe("ParserTest", () => {
  it("draws the badge of the resolution the page gives a row on its card, and none on a card of a row that has none", () => {
    const w = mount(ParserTest, { props: { profileId: 5 }, global: { plugins: [PrimeVue] } })
    const cards = w.findAll(".card-test-result")
    expect(cards.map((card) => card.find(".quality-badge").exists() ? card.find(".quality-badge").text() : null)).toEqual([
      "4K 2160", null, null,
    ])
  })

  it("takes the rows of a card the search of an application found by its address, which stays in the field", async () => {
    const w = mount(ParserTest, { props: { profileId: 5 }, global: { plugins: [PrimeVue] } })
    await w.findAll(".source-mode .st-tab")[1].trigger("click")
    await w.find(".app-query input").setValue("万界独尊")
    await w.find(".app-find").trigger("click")
    await flushPromises()
    await w.find(".app-card .app-add").trigger("click")
    await flushPromises()
    expect(readPage).toHaveBeenCalledWith("app://banri/219017", "")
    expect((w.find("input").element as HTMLInputElement).value).toBe("app://banri/219017")
  })
})
