import { describe, expect, it } from "vitest"
import {
  blocksAdd, sourceRows, type PlayerReport, type SourcesCoverage, type SourcesLayout, type SourcesState,
} from "./webSources"

const PAGE = "https://1.ruchime.org/release/test"
const players = (over: Record<string, Partial<PlayerReport>> = {}): Record<string, PlayerReport> => ({
  vk: { present: true, available: true, enabled: true, toggle: `${PAGE}?off=vk`, ...over.vk },
  allvideo: { present: true, available: true, enabled: true, toggle: `${PAGE}?off=allvideo`, ...over.allvideo },
  vibix: { present: true, available: null, enabled: true, toggle: `${PAGE}?off=vibix`, ...over.vibix },
})
const season = (position: number, count: number, ranges: string) => ({
  url: `${PAGE}?vibix=${position}`, studio: `Сезон ${position}`, ranges, count,
  resolutions: [{ resolution: 1080, ranges, count }],
})
const catalog = (selected: string | null = null): SourcesLayout => ({
  sources: [season(1, 9, "1-9"), season(2, 14, "1-14")], selected_source: selected, reason: "",
})
const coverage: SourcesCoverage = {
  players: {
    vk: { present: 13, available: 13, count: 13, ranges: "1-9, 11-14", resolutions: [
      { resolution: 1080, ranges: "1-9", count: 9 }, { resolution: 720, ranges: "11-14", count: 4 }] },
    allvideo: { present: 14, available: 14, count: 14, ranges: "1-14", resolutions: [
      { resolution: 720, ranges: "1-14", count: 14 }] },
  },
}
const state = (over: Partial<SourcesState> = {}): SourcesState => ({
  players: players(), layout: catalog(), coverage, layoutLoading: false, coverageLoading: false, ...over,
})
const row = (rows: ReturnType<typeof sourceRows>, key: string) => rows.find((r) => r.key === key)!

describe("sourceRows", () => {
  it("lists the players in the order the provider tries them", () => {
    expect(sourceRows(state()).map((r) => r.key)).toEqual(["vk", "allvideo", "vibix"])
  })

  it("reads every source while the episodes and the catalog are being read", () => {
    const rows = sourceRows(state({ layout: null, coverage: null, layoutLoading: true, coverageLoading: true }))
    expect(rows.map((r) => r.state)).toEqual(["reading", "reading", "reading"])
    expect(blocksAdd(rows)).toBe(true)
  })

  it("shows the count and the resolution by ranges of a page player once its episodes are read", () => {
    const rows = sourceRows(state())
    expect(row(rows, "vk")).toMatchObject({
      state: "available", count: 13, groups: [{ label: "1080p", ranges: "1-9" }, { label: "720p", ranges: "11-14" }],
    })
    expect(row(rows, "allvideo")).toMatchObject({ state: "available", count: 14, groups: [{ label: "720p", ranges: "1-14" }] })
  })

  it("lets the catalog arrive before the episodes are read", () => {
    const rows = sourceRows(state({ layout: catalog(`${PAGE}?vibix=2`), coverage: null, coverageLoading: true }))
    expect(row(rows, "vk").state).toBe("reading")
    expect(row(rows, "vibix")).toMatchObject({ state: "available", count: 14 })
    expect(blocksAdd(rows)).toBe(false)
  })

  it("asks for a season when the catalog has several and none is chosen", () => {
    const rows = sourceRows(state())
    expect(row(rows, "vibix").state).toBe("choose")
    expect(row(rows, "vibix").seasons.map((s) => [s.name, s.ranges, s.count])).toEqual([["Сезон 1", "1-9", 9], ["Сезон 2", "1-14", 14]])
    expect(blocksAdd(rows)).toBe(true)
  })

  it("shows the count and the resolution by ranges of the chosen season", () => {
    const rows = sourceRows(state({ layout: catalog(`${PAGE}?vibix=2`) }))
    expect(row(rows, "vibix")).toMatchObject({ state: "available", count: 14, selected: `${PAGE}?vibix=2`, groups: [{ label: "1080p", ranges: "1-14" }] })
    expect(blocksAdd(rows)).toBe(false)
  })

  it("needs no choice when the catalog has one season", () => {
    const only: SourcesLayout = { sources: [season(1, 9, "1-9")], selected_source: `${PAGE}?vibix=1` }
    const rows = sourceRows(state({ layout: only }))
    expect(row(rows, "vibix")).toMatchObject({ state: "available", count: 9 })
    expect(blocksAdd(rows)).toBe(false)
  })

  it("lets a disabled source go: it blocks nothing and shows no data", () => {
    const rows = sourceRows(state({ players: players({ vibix: { enabled: false }, allvideo: { enabled: false } }) }))
    expect(rows.filter((r) => !r.enabled).map((r) => [r.key, r.state])).toEqual([["allvideo", "off"], ["vibix", "off"]])
    expect(row(rows, "vibix").groups).toEqual([])
    expect(blocksAdd(rows)).toBe(false)
  })

  it("keeps the address that flips a player", () => {
    expect(sourceRows(state())[0].toggle).toBe(`${PAGE}?off=vk`)
  })

  it("tells a player that no episode shows from one that shows and does not play", () => {
    const given: SourcesCoverage = { players: {
      vk: { present: 0, available: 0, count: 0, ranges: "" },
      allvideo: { present: 3, available: 0, count: 0, ranges: "" },
    } }
    const rows = sourceRows(state({ coverage: given }))
    expect([row(rows, "vk").state, row(rows, "allvideo").state]).toEqual(["absent", "unavailable"])
  })

  it("falls back to the preview of the release page when the episodes could not be read", () => {
    const failed: SourcesCoverage = { players: {}, reason: "{\"code\":\"pages_unread\"}" }
    const rows = sourceRows(state({ coverage: failed, players: players({ vk: { available: false }, allvideo: { present: false } }) }))
    expect([row(rows, "vk").state, row(rows, "allvideo").state]).toEqual(["unavailable", "absent"])
  })

  it("marks a catalog that could not be read as unavailable and does not block", () => {
    const rows = sourceRows(state({ layout: { sources: [], reason: "{\"code\":\"player_failed\"}" } }))
    expect(row(rows, "vibix").state).toBe("unavailable")
    expect(blocksAdd(rows)).toBe(false)
  })

  it("says a page without the Vibix player has none", () => {
    const rows = sourceRows(state({ layout: { sources: [], reason: "{\"code\":\"page_no_player\",\"player\":\"vibix\"}" } }))
    expect(row(rows, "vibix").state).toBe("absent")
  })
})
