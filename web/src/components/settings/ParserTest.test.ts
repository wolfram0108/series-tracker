// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest"
import { flushPromises, mount } from "@vue/test-utils"
import { ref } from "vue"
import PrimeVue from "primevue/config"
import ParserTest from "./ParserTest.vue"

const readPage = vi.fn()
const clear = vi.fn()
vi.mock("../../composables/useRulesTest", () => ({
  useRulesTest: () => ({
    lines: ref([
      { key: "a", line: "Тайтл | VK | 2160p | Серия 1", result: null, heights: [1080, 2160] },
      { key: "b", line: "Тайтл | VK | Серия 2", result: null, heights: [] },
      { key: "c", line: "Серия 3, набранная в поле", result: null },
    ]),
    state: ref("ready"), reason: ref(null), error: ref(""), progress: ref(null), fetched: ref([]),
    readPage, runLines: vi.fn(), loadFilenames: vi.fn(), clear,
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

  const SERIES = [
    { url: "https://dunhuatv.ru/17.html", vk: false },
    { url: "https://vkvideo.ru/@kino|тайтл/второй", vk: true },
  ]
  const open = async (w: ReturnType<typeof mount>, index: number) => {
    await w.findAll(".pick-trigger")[index].trigger("click")
    await flushPromises()
  }
  const listed = () => [...document.body.querySelectorAll(".pick-list .option")].map((each) => each.textContent)
  const clickOption = async (index: number) => {
    ;(document.body.querySelectorAll(".pick-list .option")[index] as HTMLElement).click()
    await flushPromises()
  }

  it("offers the sources of the series by their addresses, and the queries of its channels, to put into the fields", async () => {
    const loadSources = vi.fn().mockResolvedValue(SERIES)
    const w = mount(ParserTest, { props: { profileId: 5, loadSources }, global: { plugins: [PrimeVue] }, attachTo: document.body })
    expect(w.findAll(".pick-trigger")).toHaveLength(2)
    await open(w, 0)
    expect(listed()).toEqual(["https://dunhuatv.ru/17.html", "https://vkvideo.ru/@kino|тайтл/второй"])
    await clickOption(1)  // a channel of VK: its link into the address, its query into the queries
    const fields = w.findAll("input.item-input").map((each) => (each.element as HTMLInputElement).value)
    expect(fields).toEqual(["https://vkvideo.ru/@kino", "тайтл/второй"])
    await open(w, 1)
    expect(listed()).toEqual(["тайтл/второй"])
    await clickOption(0)
    expect((w.findAll("input.item-input")[1].element as HTMLInputElement).value).toBe("тайтл/второй")
    w.unmount()
  })

  it("reads the sources anew at every opening, and offers nothing where it stands in no series", async () => {
    const loadSources = vi.fn().mockResolvedValueOnce([]).mockResolvedValueOnce(SERIES)
    const w = mount(ParserTest, { props: { profileId: 5, loadSources }, global: { plugins: [PrimeVue] }, attachTo: document.body })
    await open(w, 0)
    expect(document.body.querySelector(".pick-empty")).not.toBeNull()
    await w.findAll(".pick-trigger")[0].trigger("click")  // closes
    await open(w, 0)
    expect(listed()).toHaveLength(2)
    expect(loadSources).toHaveBeenCalledTimes(2)
    w.unmount()
    const alone = mount(ParserTest, { props: { profileId: 5 }, global: { plugins: [PrimeVue] } })
    expect(alone.findAll(".pick-trigger")).toHaveLength(0)
  })

  it("forgets the answers of the profile when the profile changes, and keeps what was typed", async () => {
    clear.mockClear()
    const w = mount(ParserTest, { props: { profileId: 5 }, global: { plugins: [PrimeVue] } })
    await w.find("textarea").setValue("Серия 1")
    await w.setProps({ profileId: 6 })
    expect(clear).toHaveBeenCalledTimes(1)
    expect((w.find("textarea").element as HTMLTextAreaElement).value).toBe("Серия 1")
  })
})
