// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest"
import { nextTick, ref } from "vue"
import { mount } from "@vue/test-utils"

const client = vi.hoisted(() => ({ GET: vi.fn(), POST: vi.fn() }))
vi.mock("../api/client", () => ({ api: client }))
const toast = vi.hoisted(() => ({ add: vi.fn() }))
vi.mock("primevue/usetoast", () => ({ useToast: () => toast }))

import { useTmdbChoice } from "./useTmdbChoice"
import TmdbSync from "../components/TmdbSync.vue"

const card = { id: 7, name: "Владыка", name_en: "Lord", original_name: "领主", year: "2024" }
function choice(season = "s02", seasonless = false) {
  const form = { name: "Имя", name_en: "Name", season: ref(season), seasonless: ref(seasonless) }
  return { form, tmdb: useTmdbChoice({ query: () => form.name || form.name_en, season: () => form.season.value, seasonless: () => form.seasonless.value }) }
}

describe("the TMDB card of a series", () => {
  beforeEach(() => {
    client.GET.mockReset()
    client.POST.mockReset()
    toast.add.mockReset()
  })

  it("is searched by what the field holds, else by the name of the series, and the cards found are kept", async () => {
    client.POST.mockResolvedValue({ data: { success: true, results: [card] } })
    const { tmdb } = choice()
    await tmdb.search()
    expect(client.POST).toHaveBeenCalledWith("/api/tmdb/search", { body: { query: "Имя" } })
    expect(tmdb.results.value).toEqual([card])
    expect(tmdb.loading.value).toBe(false)
  })

  it("says it where the search was refused, and keeps no cards", async () => {
    client.POST.mockResolvedValue({ data: { success: false, error: "x" } })
    const { tmdb } = choice()
    await tmdb.search()
    expect(toast.add).toHaveBeenCalledOnce()
    expect(tmdb.results.value).toEqual([])
  })

  it("counts the episodes of the season the form asks for, again when the season changes, and of one for a series of every season", async () => {
    client.GET.mockResolvedValue({ data: { success: true, seasons: [{ season_number: 1, episode_count: 12 }, { season_number: 2, episode_count: 24 }] } })
    const { form, tmdb } = choice("s02")
    await tmdb.select(card)
    expect([tmdb.seasonNumber.value, tmdb.episodeCount.value]).toEqual([2, 24])
    form.season.value = "s01"
    await nextTick()
    expect(tmdb.episodeCount.value).toBe(12)
    form.seasonless.value = true
    expect(tmdb.seasonNumber.value).toBe(1)
    tmdb.clear()
    expect([tmdb.selected.value, tmdb.episodeCount.value]).toEqual([null, 0])
  })

  it("names the folder of the catalog by the name the user reads: the translated one when there is one, else the English, the year when known", () => {
    const { tmdb } = choice()
    expect(tmdb.name(card)).toBe("Владыка")
    expect(tmdb.name({ id: 1, name: "领主", original_name: "领主", name_en: "Lord" })).toBe("Lord")
    tmdb.selected.value = card
    expect(tmdb.catalogName.value).toBe("Владыка (2024) [tmdbid-7]")
    tmdb.selected.value = { id: 9, name: "A", original_name: "B" }
    expect(tmdb.catalogName.value).toBe("A [tmdbid-9]")
  })
})

describe("the TMDB row", () => {
  it("shows the cards found, chooses one by a press, and lets the choice go by the keyboard", async () => {
    client.GET.mockResolvedValue({ data: { success: true, seasons: [{ season_number: 1, episode_count: 12 }] } })
    const { tmdb } = choice("s01")
    tmdb.results.value = [card]
    const row = mount(TmdbSync, { props: { tmdb }, attachTo: document.body })
    await row.find(".tmdb-result-card").trigger("click")
    await vi.waitFor(() => expect(tmdb.selected.value?.id).toBe(7))
    await nextTick()
    const reset = row.find(".pill-link")
    expect(reset.attributes("tabindex")).toBe("0")
    await reset.trigger("keydown", { key: "Enter" })
    expect(tmdb.selected.value).toBeNull()
    row.unmount()
  })
})
