// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest"
import { mount } from "@vue/test-utils"
import { ref } from "vue"
import PrimeVue from "primevue/config"
import ParserTest from "./ParserTest.vue"

const readPage = vi.fn()
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
})
