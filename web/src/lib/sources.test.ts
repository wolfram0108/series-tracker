import { describe, expect, it } from "vitest"
import {
  draftBody, heightText, itemOfSource, joinVkAddress, phaseText, playerCaption, profileOptions, progressText,
  readOptions, seasonPositions, siteOf, sourceCaption, sourceTypeOf, splitVkAddress, stepsText, summaryOf, togglePlayer,
  viewOf, vkModeOf, type PlayerSummary, type SeriesSource, type SourceSummary,
} from "./sources"
import type { PageStatus } from "../stores/sourceTables"

// Samples have the shape of CompositionPreviewReply.summary in api/schema.d.ts: the page of a release with four
// players and a catalog of two seasons, and a channel of VK whose rows have no voice-over of their own.
const vk: PlayerSummary = {
  player: "vk", player_label: "VK", rows: 13, groups: 0, heights: [720, 1080],
  voices: [{ name: "Ruchime", group: "Ruchime", rows: 13 }],
}
const vibix: PlayerSummary = {
  player: "vibix", player_label: "Vibix", rows: 23, groups: 2, heights: [1080],
  voices: [{ name: "Ruchime", group: "Ruchime", rows: 23 }],
}
const kodik: PlayerSummary = {
  player: "kodik", player_label: "", rows: 5, groups: 1, heights: [480, 720],
  voices: [{ name: "Студия Константина Исаева", group: null, rows: 3 }, { name: "AniStar", group: "AniStar", rows: 2 }],
}
const release: SourceSummary = {
  source_id: 7, url: "https://1.ruchime.org/release/test", status: "no_profile", reason: null,
  read_at: "2026-11-01T10:00:00Z", players: [vk, vibix, kodik],
}
const idle: PageStatus = { state: "ready", progress: null, reason: null, readAt: null }

describe("viewOf", () => {
  it("shows the players of the table with their counts and steps, never a number of an episode", () => {
    const view = viewOf({ summary: release, playersOff: [] })
    expect(view.state).toBe("noProfile")
    expect(view.players.map((p) => [p.title, p.rows, p.groups, p.topHeight])).toEqual([
      ["VK", 13, 0, 1080], ["Vibix", 23, 2, 1080], ["kodik", 5, 1, 720],
    ])
    expect(view.players[0].steps).toEqual([1080, 720])
  })

  it("takes the label of a player from the table and falls back to the kind of the player", () => {
    const players = viewOf({ summary: release, playersOff: [] }).players
    expect([players[0].title, players[2].title]).toEqual(["VK", "kodik"])
  })

  it("keeps the group of a voice-over and marks a voice-over that is in no group", () => {
    const voices = viewOf({ summary: release, playersOff: [] }).players[2].voices
    expect(voices.map((v) => [v.name, v.group])).toEqual([["Студия Константина Исаева", null], ["AniStar", "AniStar"]])
  })

  it("offers the choice of a season only for a catalog with several seasons", () => {
    const choose = (player: PlayerSummary) => viewOf({
      summary: { ...release, players: [player] }, playersOff: [],
    }).players[0].chooseSeason
    expect(choose(vibix)).toBe(true)
    expect(choose({ ...vibix, groups: 1 })).toBe(false)
    expect(choose({ ...kodik, groups: 3 })).toBe(false)
  })

  it("marks the players the source switched off", () => {
    const players = viewOf({ summary: release, playersOff: ["vibix"] }).players
    expect(players.map((p) => p.off)).toEqual([false, true, false])
  })

  it("follows the summary status when no news of the page came", () => {
    const state = (status: SourceSummary["status"], reason: string | null = null) =>
      viewOf({ summary: { ...release, status, reason }, playersOff: [] })
    expect(state("ready").state).toBe("ready")
    expect(state("waiting").state).toBe("waiting")
    expect(state("failed", '{"code":"read_failed"}')).toMatchObject({ state: "failed", reason: '{"code":"read_failed"}' })
    expect(viewOf({ summary: null, playersOff: [] }).state).toBe("waiting")
  })

  it("lets the progress of the core win over the stored summary and carries it", () => {
    const live: PageStatus = { state: "reading", progress: { phase: "episodes", current: 3, total: 20 }, reason: null, readAt: null }
    const view = viewOf({ summary: { ...release, status: "ready" }, playersOff: [], live })
    expect(view).toMatchObject({ state: "reading", progress: { phase: "episodes", current: 3, total: 20 } })
    expect(view.players).toHaveLength(3)
  })

  it("shows a failed read together with the earlier table that stays in force", () => {
    const live: PageStatus = { state: "failed", progress: null, reason: '{"code":"read_timeout","seconds":900}', readAt: null }
    const view = viewOf({ summary: release, playersOff: [], live })
    expect(view).toMatchObject({ state: "failed", reason: '{"code":"read_timeout","seconds":900}' })
    expect(view.players).toHaveLength(3)
  })

  it("settles on the stored summary once the core said the page is ready", () => {
    expect(viewOf({ summary: { ...release, status: "ready" }, playersOff: [], live: idle }).state).toBe("ready")
  })

  it("follows the answer of a preview request for a draft that is no source yet", () => {
    expect(viewOf({ summary: null, playersOff: [], asked: { state: "reading" } }).state).toBe("reading")
    expect(viewOf({ summary: null, playersOff: [], asked: { state: "absent" } }).state).toBe("absent")
    expect(viewOf({ summary: release, playersOff: [], asked: { state: "failed", reason: "{\"code\":\"x\"}" } }))
      .toMatchObject({ state: "failed", reason: "{\"code\":\"x\"}" })
    expect(viewOf({ summary: release, playersOff: [], asked: { state: "ready" } }).state).toBe("noProfile")
  })
})

describe("texts", () => {
  const view = viewOf({ summary: release, playersOff: [] })

  it("counts players and rows of a source with the right plural forms", () => {
    expect(sourceCaption(view.players)).toBe("3 плеера, 41 строка")
    expect(sourceCaption([view.players[0]])).toBe("1 плеер, 13 строк")
  })

  it("counts the rows and the seasons of a player, and leaves out seasons it does not keep", () => {
    expect(playerCaption(view.players[1])).toBe("23 строки, 2 сезона")
    expect(playerCaption(view.players[0])).toBe("13 строк")
    expect(playerCaption({ ...view.players[2], rows: 21, groups: 1 })).toBe("21 строка, 1 сезон")
    expect(playerCaption({ ...view.players[2], rows: 11, groups: 5 })).toBe("11 строк, 5 сезонов")
  })

  it("names the top step and all steps of a player", () => {
    expect(heightText(view.players[0])).toBe("до 1080p")
    expect(stepsText(view.players[2])).toBe("720p, 480p")
    expect(heightText({ ...view.players[0], topHeight: null, steps: [] })).toBe("")
  })

  it("words the progress of a read by phase and by how far it is", () => {
    expect(progressText({ phase: "episodes", current: 3, total: 20 })).toBe("Страницы серий: 3 из 20")
    expect(progressText({ phase: "catalog", current: 0, total: 0 })).toBe("Каталог плеера")
    expect(phaseText("from_the_future")).toBe("Чтение страницы")
  })
})

describe("the choices of a source", () => {
  it("switches a player off and on again", () => {
    expect(togglePlayer([], "kodik")).toEqual(["kodik"])
    expect(togglePlayer(["kodik", "vk"], "kodik")).toEqual(["vk"])
  })

  it("counts the seasons of a catalog from 1, as the core does", () => {
    expect(seasonPositions(viewOf({ summary: release, playersOff: [] }).players[1].groups)).toEqual([1, 2])
  })

  it("builds the options of the profile with the absence of a profile first", () => {
    expect(profileOptions([{ id: 4, name: "ruchime" }])).toEqual([
      { label: "Не назначен", value: null }, { label: "ruchime", value: 4 },
    ])
  })
})

describe("addresses", () => {
  it("makes a source of a channel of VK a video source of VK and any other site a web one", () => {
    expect(sourceTypeOf({ site: "vk_channel", canonical_url: "https://vkvideo.ru/@kino||search" })).toBe("vk_video")
    expect(sourceTypeOf({ site: "ruchime", canonical_url: "https://1.ruchime.org/release/test" })).toBe("web_video")
    expect(sourceTypeOf({ site: null, canonical_url: null })).toBeNull()
  })

  it("splits the address of a channel into the channel and the query, and joins them back", () => {
    expect(splitVkAddress("https://vkvideo.ru/@kino|тайтл/второй")).toEqual({ channel: "https://vkvideo.ru/@kino", query: "тайтл/второй" })
    expect(splitVkAddress("https://vkvideo.ru/@kino?q=тайтл")).toEqual({ channel: "https://vkvideo.ru/@kino", query: "тайтл" })
    expect(splitVkAddress("https://vkvideo.ru/@kino")).toEqual({ channel: "https://vkvideo.ru/@kino", query: "" })
    expect(joinVkAddress(" https://vkvideo.ru\\@kino ", " тайтл ")).toBe("https://vkvideo.ru/@kino|тайтл")
  })

  it("takes the host of a source as the site of the series, without www and without the query of a channel", () => {
    expect(siteOf("https://www.dunhuatv.ru/anime/title")).toBe("dunhuatv.ru")
    expect(siteOf("https://vkvideo.ru/@kino|тайтл")).toBe("vkvideo.ru")
    expect(siteOf("not an address")).toBe("")
  })

  it("falls back to the search mode that works when the mode is not one of the known", () => {
    expect([vkModeOf("get_all"), vkModeOf("search"), vkModeOf("x"), vkModeOf(null)]).toEqual(["get_all", "search", "search", "search"])
  })

  it("asks the site with the mode of a channel and with nothing for other sources", () => {
    expect(readOptions({ sourceType: "vk_video", vkSearchMode: "get_all" })).toEqual({ vk_search_mode: "get_all" })
    expect(readOptions({ sourceType: "web_video", vkSearchMode: "search" })).toEqual({})
  })

  it("sends a draft with exactly the fields of a source", () => {
    expect(draftBody({
      url: "https://1.ruchime.org/release/test", sourceType: "web_video", profileId: 4, vkSearchMode: "search",
      vibixSeason: 2, playersOff: ["kodik"],
    })).toEqual({
      url: "https://1.ruchime.org/release/test", source_type: "web_video", parser_profile_id: 4, vk_search_mode: "search",
      vibix_season: 2, players_off: ["kodik"],
    })
  })
})

describe("the sources of a stored series", () => {
  const stored: SeriesSource = {
    id: 7, series_id: 1, position: 0, url: "https://1.ruchime.org/release/test", source_type: "web_video",
    parser_profile_id: null, vk_search_mode: "search", vibix_season: 2, players_off: ["kodik"],
  }

  it("finds the summary of a source by its id", () => {
    expect(summaryOf([release], 7)).toBe(release)
    expect(summaryOf([release], 8)).toBeNull()
  })

  it("makes an item from the source and its summary", () => {
    const item = itemOfSource(stored, release, null)
    expect(item).toMatchObject({ id: 7, sourceType: "web_video", profileId: null, vibixSeason: 2, playersOff: ["kodik"] })
    expect(item.view.players.map((p) => p.off)).toEqual([false, false, true])
  })

  it("makes an item of a source whose table is not in yet", () => {
    expect(itemOfSource({ ...stored, players_off: undefined }, null, null).view).toMatchObject({ state: "waiting", players: [] })
  })
})
