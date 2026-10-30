import { describe, expect, it } from "vitest"
import { blocksAdd, sourceRows, type PlayerReport, type SourcesLayout } from "./webSources"

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
const vibix = (rows: ReturnType<typeof sourceRows>) => rows.find((row) => row.key === "vibix")!

describe("sourceRows", () => {
  it("lists the players in the order the provider tries them", () => {
    expect(sourceRows(players(), catalog(), false).map((row) => row.key)).toEqual(["vk", "allvideo", "vibix"])
  })

  it("reads the catalog of Vibix while it is being read", () => {
    const rows = sourceRows(players(), null, true)
    expect(vibix(rows).state).toBe("reading")
    expect(blocksAdd(rows)).toBe(true)
  })

  it("asks for a season when the catalog has several and none is chosen", () => {
    const rows = sourceRows(players(), catalog(), false)
    expect(vibix(rows).state).toBe("choose")
    expect(vibix(rows).seasons.map((s) => [s.name, s.ranges, s.count])).toEqual([["Сезон 1", "1-9", 9], ["Сезон 2", "1-14", 14]])
    expect(blocksAdd(rows)).toBe(true)
  })

  it("shows the count and the resolution by ranges of the chosen season", () => {
    const rows = sourceRows(players(), catalog(`${PAGE}?vibix=2`), false)
    expect(vibix(rows)).toMatchObject({ state: "available", count: 14, selected: `${PAGE}?vibix=2`, groups: [{ label: "1080p", ranges: "1-14" }] })
    expect(blocksAdd(rows)).toBe(false)
  })

  it("needs no choice when the catalog has one season", () => {
    const only: SourcesLayout = { sources: [season(1, 9, "1-9")], selected_source: `${PAGE}?vibix=1` }
    const rows = sourceRows(players(), only, false)
    expect(vibix(rows)).toMatchObject({ state: "available", count: 9 })
    expect(blocksAdd(rows)).toBe(false)
  })

  it("lets a disabled source go: it blocks nothing and shows no data", () => {
    const rows = sourceRows(players({ vibix: { enabled: false }, allvideo: { enabled: false } }), catalog(), false)
    expect(rows.filter((row) => !row.enabled).map((row) => [row.key, row.state])).toEqual([["allvideo", "off"], ["vibix", "off"]])
    expect(vibix(rows).groups).toEqual([])
    expect(blocksAdd(rows)).toBe(false)
  })

  it("keeps the address that flips a player", () => {
    expect(sourceRows(players(), catalog(), false)[0].toggle).toBe(`${PAGE}?off=vk`)
  })

  it("names the states of the preview players", () => {
    const rows = sourceRows(players({ vk: { available: false }, allvideo: { present: false } }), catalog(), false)
    expect(rows.map((row) => row.state)).toEqual(["unavailable", "absent", "choose"])
  })

  it("marks a catalog that could not be read as unavailable and does not block", () => {
    const rows = sourceRows(players(), { sources: [], reason: "{\"code\":\"player_failed\"}" }, false)
    expect(vibix(rows).state).toBe("unavailable")
    expect(blocksAdd(rows)).toBe(false)
  })

  it("treats a missing preview report of a player as pending, not as an error", () => {
    expect(sourceRows(players({ vk: { available: null } }), catalog(), false)[0].state).toBe("pending")
  })
})
