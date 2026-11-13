import { describe, expect, it } from "vitest"
import {
  coverageOf, draftBody, itemOfSource, joinVkAddress, pageHost, phaseText, placeOf, profileOptions, progressText,
  readEnded, seasonPositions, siteOf, sourceCaption, sourceTypeOf, spanText, splitVkAddress, stepsLines, summaryOf, testOffers, togglePlayer,
  titlesOf, viewOf, voicesOf, type LayoutRow, type PlayerSummary, type SeriesSource, type SourceSummary,
} from "./sources"
import type { PageStatus } from "../stores/sourceTables"

// Samples have the shape of CompositionPreviewReply.summary in api/schema.d.ts: the page of a release with four
// players and a catalog of two seasons, and a channel of VK whose rows have no voice-over of their own.
const vk: PlayerSummary = { player: "vk", player_label: "VK", rows: 13, groups: 0 }
const vibix: PlayerSummary = { player: "vibix", player_label: "Vibix", rows: 23, groups: 2 }
const kodik: PlayerSummary = { player: "kodik", player_label: "", rows: 5, groups: 1 }
const release: SourceSummary = {
  source_id: 7, url: "https://1.ruchime.org/release/test", status: "no_profile", title: "", reason: null,
  read_at: "2026-11-01T10:00:00Z", players: [vk, vibix, kodik],
}
const idle: PageStatus = { state: "ready", progress: null, reason: null, readAt: null }

describe("viewOf", () => {
  it("shows the players of the table with their counts, and no line of episodes or voice-overs without rows of the constructor", () => {
    const view = viewOf({ summary: release, playersOff: [] })
    expect(view.state).toBe("noProfile")
    expect(view.players.map((p) => [p.title, p.rows, p.groups])).toEqual([["VK", 13, 0], ["Vibix", 23, 2], ["kodik", 5, 1]])
    expect(view.players.map((p) => [p.coverage, p.voices, p.steps])).toEqual([[[], [], null], [[], [], null], [[], [], null]])
  })

  it("takes the label of a player from the table and falls back to the kind of the player", () => {
    const players = viewOf({ summary: release, playersOff: [] }).players
    expect([players[0].title, players[2].title]).toEqual(["VK", "kodik"])
  })

  it("builds the lines of a player from the rows of the layout of that player and of no other", () => {
    const rows = [
      layoutRow({ player: "vk", episode_start: 1, episode_end: 3 }),
      layoutRow({ player: "kodik", row_key: "k", episode_start: 5, episode_end: 5 }),
    ]
    const players = viewOf({ summary: release, playersOff: [], rows }).players
    expect(players.map((p) => p.coverage.map(spanText))).toEqual([["1–3"], [], ["5"]])
    expect(players.map((p) => p.steps)).toEqual(["720p–1080p", null, "720p–1080p"])
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
    const live: PageStatus = { state: "reading", progress: { phase: "episodes", current: 3, total: 20, rate: null }, reason: null, readAt: null }
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

const layoutRow = (over: Partial<LayoutRow> = {}): LayoutRow => ({
  series_id: 1, source_id: 7, in_output: true, row_key: "r", excluded: false, season: 1, episode_start: 1, episode_end: 1,
  voice_label: "Ruchime", voice_name: "Ruchime", voice_group: null, player: "vk", player_label: "VK", group: "", text: "",
  heights: [720, 1080], state: "playable", link: "", video: "", stale: false, ...over,
})

describe("what the source did not give in its last read", () => {
  it("is named by episode in the line of its voice-over, and a voice-over or a player with no current episode is stale whole", () => {
    const rows = [1, 2, 3, 4].map((episode) => layoutRow({ row_key: `e${episode}`, episode_start: episode, episode_end: episode,
      player: "cvh", player_label: "CVH", stale: episode > 2 }))
    const [voice] = voicesOf(rows)
    expect([voice.stale, voice.allStale]).toEqual([["3–4"], false])
    const [gone] = voicesOf(rows.map((row) => ({ ...row, stale: true })))
    expect([gone.stale, gone.allStale]).toEqual([["1–4"], true])
    const summary = { player: "cvh", player_label: "CVH", rows: 4, groups: 0, group_names: [] }
    expect(viewOf({ summary: { ...release, players: [summary] }, playersOff: [], rows: rows.map((row) => ({ ...row, stale: true })) })
      .players[0].allStale).toBe(true)
    expect(viewOf({ summary: { ...release, players: [summary] }, playersOff: [], rows }).players[0].allStale).toBe(false)
  })
})

describe("the lines of a player from the rows of the constructor", () => {
  const pva = layoutRow({ row_key: "a", episode_start: 1, episode_end: 10, heights: [480, 1080, 2160], voice_name: "ПВА ШОУ", voice_label: "ПВА ШОУ", voice_group: "ПВА ШОУ" })
  const two = layoutRow({ row_key: "b", episode_start: 11, episode_end: 50, heights: [480, 720], voice_name: "", voice_label: "Неизвестная озвучка 2" })
  const late = layoutRow({ row_key: "c", episode_start: 61, episode_end: 120, heights: [360, 1440], voice_name: "", voice_label: "Неизвестная озвучка 2" })

  it("joins the episodes that follow one another into one span and names the episodes missing inside it", () => {
    expect(coverageOf([pva, two, late])).toEqual([{
      season: null, span: "1–120", gaps: "51–60",
      steps: [{ ranges: "1–50", steps: "480p–2160p" }, { ranges: "61–120", steps: "360p–1440p" }],
    }])
  })

  it("gives one resolution when every range shares it, and the ranges of each resolution when they differ", () => {
    // The layout of a release of the server: three single episodes missing, every range at the same steps.
    const gapped = [[330, 346], [348, 463], [465, 479], [481, 496]].map(([from, to], index) =>
      layoutRow({ row_key: `g${index}`, episode_start: from, episode_end: to, heights: [360, 1080] }))
    expect(coverageOf(gapped)).toEqual([{ season: null, span: "330–496", gaps: "347, 464, 480", steps: [{ ranges: null, steps: "360p–1080p" }] }])
    expect(stepsLines(coverageOf(gapped))).toEqual(["360p–1080p"])
    expect(stepsLines(coverageOf([pva, two, late]))).toEqual(["1–50: 480p–2160p", "61–120: 360p–1440p"])
  })

  it("gives a voice-over a row with its group, its name, its episodes and its rows, and no group when it has none", () => {
    expect(voicesOf([pva, two, late]).map((voice) => [voice.group, voice.name, voice.coverage.map(spanText), voice.coverage[0].gaps, voice.rows]))
      .toEqual([["ПВА ШОУ", "ПВА ШОУ", ["1–10"], "", 1], [null, "Неизвестная озвучка 2", ["11–120"], "51–60", 2]])
  })

  it("names a season only when there are several, and takes a single episode as a number", () => {
    expect(coverageOf([layoutRow({ season: 1, episode_start: 3, episode_end: 3, heights: [] })]).map(spanText)).toEqual(["3"])
    const two = coverageOf([layoutRow({ season: 1 }), layoutRow({ season: 2, episode_start: 4, episode_end: 6, heights: [1080] })])
    expect(two.map(spanText)).toEqual(["Сезон 1: 1", "Сезон 2: 4–6"])
    expect(stepsLines(two)).toEqual(["720p–1080p", "1080p"])
  })

  it("leaves out the rows the constructor excluded or gave no number", () => {
    const lost = [layoutRow({ excluded: true }), layoutRow({ episode_start: null, episode_end: null })]
    expect([coverageOf(lost), voicesOf(lost)]).toEqual([[], []])
  })

  it("names a source by its site, and a channel of VK by the site and the channel, as the output tab does", () => {
    expect(placeOf("https://dunhuatv.ru/761-vechnaja-volja.html")).toBe("dunhuatv.ru")
    expect(placeOf("https://vkvideo.ru/@mir.donghua|Вечная воля")).toBe("vkvideo.ru @mir.donghua")
    expect(placeOf("not an address")).toBe("not an address")
  })
})

describe("texts", () => {
  const view = viewOf({ summary: release, playersOff: [] })

  it("counts players and rows of a source with the right plural forms", () => {
    expect(sourceCaption(view.players)).toBe("3 плеера, 41 строка")
    expect(sourceCaption([view.players[0]])).toBe("1 плеер, 13 строк")
  })

  it("words the progress of a read by phase and by how far it is", () => {
    expect(progressText({ phase: "episodes", current: 3, total: 20, rate: null })).toBe("Страницы серий: 3 из 20")
    expect(progressText({ phase: "episodes", current: 34, total: 391, rate: 0.625 })).toBe("Страницы серий: 34 из 391 | 0.6 с/с")
    expect(progressText({ phase: "catalog", current: 0, total: 0, rate: null })).toBe("Каталог плеера")
    expect(phaseText("from_the_future")).toBe("Чтение страницы")
  })

  it("names the site of a page by its address, a VK channel without its name and mode", () => {
    expect(pageHost("https://1.ruchime.org/release/x")).toBe("1.ruchime.org")
    expect(pageHost("https://vkvideo.ru/@pvashow|Почтенный мастер|search")).toBe("vkvideo.ru")
    expect(pageHost("not an address")).toBe("not an address")
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

  it("sends a draft with exactly the fields of a source", () => {
    expect(draftBody({
      url: "https://1.ruchime.org/release/test", sourceType: "web_video", profileId: 4,
      vibixSeason: 2, playersOff: ["kodik"], rowsPerLine: "-10",
    })).toEqual({
      url: "https://1.ruchime.org/release/test", source_type: "web_video", parser_profile_id: 4,
      vibix_season: 2, players_off: ["kodik"], rows_per_line: "-10",
    })
  })
})

describe("the sources of a stored series", () => {
  const stored: SeriesSource = {
    id: 7, series_id: 1, position: 0, url: "https://1.ruchime.org/release/test", source_type: "web_video",
    parser_profile_id: null, vibix_season: 2, players_off: ["kodik"], rows_per_line: "all", color: 0,
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

describe("readEnded", () => {
  const was: PageStatus = { state: "reading", progress: null, reason: null, readAt: null }
  const done: PageStatus = { state: "ready", progress: null, reason: null, readAt: "2026-11-01T10:00:00Z" }

  it("asks again only for a page that was being read when the answer was taken and whose read has a later result", () => {
    expect(readEnded({ state: "reading" }, done, null)).toBe(true)
    expect(readEnded({ state: "reading" }, { ...done, state: "failed" }, was)).toBe(true)
  })

  it("does not ask for news the answer already knew, for a read still going on, or for an answer that was not reading", () => {
    expect(readEnded({ state: "reading" }, done, done)).toBe(false)
    expect(readEnded({ state: "reading" }, was, null)).toBe(false)
    expect(readEnded({ state: "reading" }, null, null)).toBe(false)
    expect(readEnded({ state: "ready" }, done, null)).toBe(false)
    expect(readEnded(null, done, null)).toBe(false)
  })
})

describe("titlesOf", () => {
  it("takes the first name in Cyrillic as the Russian one and the first in Latin letters as the English one", () => {
    expect(titlesOf(["Любовь под гипнозом", "Hypnosis love", "Кохання під гіпнозом"]))
      .toEqual({ ru: "Любовь под гипнозом", en: "Hypnosis love" })
    expect(titlesOf(["Steel Ball Run", "Невероятное приключение ДжоДжо: Гонка «Стальной шар» "]))
      .toEqual({ ru: "Невероятное приключение ДжоДжо: Гонка «Стальной шар»", en: "Steel Ball Run" })
  })

  it("leaves a name empty when the site gives none in its script", () => {
    expect(titlesOf(["Только русское"])).toEqual({ ru: "Только русское", en: "" })
    expect(titlesOf(["鬥破蒼穹", "Doupo Cangqiong"])).toEqual({ ru: "", en: "Doupo Cangqiong" })
    expect(titlesOf([])).toEqual({ ru: "", en: "" })
  })
})

describe("testOffers", () => {
  it("offers each source by its address, a channel of VK by its link with its query apart, and every query once", () => {
    const offers = testOffers([
      { url: "https://dunhuatv.ru/17.html", vk: false },
      { url: "https://vkvideo.ru/@kino|тайтл/второй", vk: true },
      { url: "https://vkvideo.ru/@other|тайтл/второй", vk: true },
      { url: "https://vkvideo.ru/@kino|третий", vk: true },
      { url: "https://dunhuatv.ru/17.html", vk: false },
    ])
    expect(offers.addresses).toEqual([
      { label: "https://dunhuatv.ru/17.html", address: "https://dunhuatv.ru/17.html", query: null },
      { label: "https://vkvideo.ru/@kino|тайтл/второй", address: "https://vkvideo.ru/@kino", query: "тайтл/второй" },
      { label: "https://vkvideo.ru/@other|тайтл/второй", address: "https://vkvideo.ru/@other", query: "тайтл/второй" },
      { label: "https://vkvideo.ru/@kino|третий", address: "https://vkvideo.ru/@kino", query: "третий" },
    ])
    expect(offers.queries).toEqual(["тайтл/второй", "третий"])
  })

  it("offers nothing for a series without sources, and no query for sites that have none", () => {
    expect(testOffers([])).toEqual({ addresses: [], queries: [] })
    expect(testOffers([{ url: "app://banri/219047", vk: false }]).queries).toEqual([])
  })
})
