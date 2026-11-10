import { describe, expect, it } from "vitest"
import type { components } from "../api/schema"
import type { VoiceDub } from "./plan"
import {
  allOff, bySeason, episodeStatus, fileNamesOf, groupTarget, isDeviation, isOff, joinEpisodes, orderValue, outputLines,
  parseGroupTarget, parseVariantTarget, perRowOf, queueLevers, referenceOf, resetBody, setBody, sourceMarks, spansOf,
  trackCells, variantTarget, voiceColumns, type EpisodeView, type Lever, type QueueRow, type Reference, type Row,
} from "./output"
import { SPECS } from "./output.fixtures"

type Schemas = components["schemas"]

// Samples have the shape of the schema: a row is one line of the layout, a reference is the answer for one episode.
const row = (over: Partial<Row> = {}): Row => ({
  series_id: 1, source_id: 7, in_output: true, row_key: "row", excluded: false, season: 1, episode_start: 1, episode_end: 1,
  voice_label: "Ruchime", voice_name: "Ruchime", player: "vibix", player_label: "", group: "", text: "",
  heights: [1080], state: "playable", link: "", video: "", stale: false, ...over,
})
const ref = (season: number, episode: number, over: Partial<Reference> = {}): Reference =>
  ({ season, episode, part: null, mark: "by_level", source_id: null, row_key: null, player: null, voice_group: null, height: null,
     reason: null, compilation: null, picture: null, markup: false, ...over })
const lever = (name: string, target: string | null | undefined, value: unknown, level: Schemas["Level"]): Lever =>
  ({ lever: name, target, value, level })
const settings = (season: number, episode: number, levers: Lever[] = []): Schemas["EpisodeSettings"] =>
  ({ season, episode, levers })
const dub = (season: number, episode: number, state: VoiceDub["state"] = "done"): VoiceDub => ({
  season, episode, part: null, voice_key: "ruchime", voice_label: "Ruchime", state, source_id: null, player: null,
  online: null, detail: null,
})
const episodeOf = (over: Partial<EpisodeView> = {}): EpisodeView => ({
  season: 1, episode: 1, references: [], rows: [], levers: [], dubs: [], files: [], labels: {}, ...over,
})

describe("joinEpisodes", () => {
  // A compilation of episodes 1-3, a single line of episode 2 from another source, an excluded line of episode 9,
  // a line with no season and a line with no number: only the first three lines make episodes.
  const compilation = row({ episode_start: 1, episode_end: 3 })
  const single = row({ source_id: 8, player: "kodik", player_label: "Kodik", episode_start: 2, episode_end: 2 })
  // What the core says of each: an excluded line, one with no season or no number, one no group took is no row of the output.
  const excluded = row({ excluded: true, episode_start: 9, episode_end: 9, in_output: false })
  const noSeason = row({ season: null, episode_start: 20, episode_end: 20, in_output: false })
  const noNumber = row({ episode_start: null, episode_end: null, in_output: false })
  const unknown = row({ episode_start: 30, episode_end: 30, voice_label: "Неизвестная озвучка 1", in_output: false })
  const lines = [compilation, single, excluded, noSeason, noNumber, unknown]

  it("joins a compilation to each of its episodes and a single line to its own", () => {
    const views = joinEpisodes(lines, [], [], [])
    expect(views.map((view) => [view.season, view.episode])).toEqual([[1, 1], [1, 2], [1, 3]])
    expect(views[0].rows).toEqual([compilation])
    expect(views[1].rows).toEqual([compilation, single])
    expect(views[2].rows).toEqual([compilation])
  })

  it("leaves out every line that is no row of the output, a line no voice-over group took among them", () => {
    const numbers = joinEpisodes(lines, [], [], []).map((view) => view.episode)
    expect(numbers).toEqual([1, 2, 3])
  })

  it("keeps an episode that only the settings name, with its levers and no rows", () => {
    const levers = [lever("voice.take", groupTarget("AniStar"), false, "episode")]
    expect(joinEpisodes([], [], [settings(1, 5, levers)], [])).toEqual([episodeOf({ episode: 5, levers, availability: {} })])
  })

  it("takes every reference of an episode in the order of its parts, the whole episode first", () => {
    const refs = [ref(1, 2, { part: 2 }), ref(1, 2, { part: 1 }), ref(1, 2, { part: null }), ref(1, 3, { part: 1 })]
    const second = joinEpisodes([single], refs, [], []).find((view) => view.episode === 2)
    expect(second?.references.map((each) => each.part)).toEqual([null, 1, 2])
    expect(second?.references).toHaveLength(3)
  })

  it("orders the episodes by season and then by episode", () => {
    const views = joinEpisodes(
      [row({ season: 2, episode_start: 1, episode_end: 1 })], [ref(1, 4)], [settings(1, 2)], [],
    )
    expect(views.map((view) => [view.season, view.episode])).toEqual([[1, 2], [1, 4], [2, 1]])
  })

  it("attaches the voice-overs of an episode and keeps an episode that only a voice-over names", () => {
    const views = joinEpisodes([single], [], [], [dub(1, 2, "error"), dub(1, 30)])
    expect(views.map((view) => view.episode)).toEqual([2, 30])
    expect(views[0].dubs).toEqual([dub(1, 2, "error")])
    expect(views[1].dubs).toEqual([dub(1, 30)]) // shown with no row and no reference, not lost
  })

  it("counts a line with no end as one episode", () => {
    const views = joinEpisodes([row({ episode_start: 6, episode_end: null })], [], [], [])
    expect(views.map((view) => view.episode)).toEqual([6])
  })
})

describe("bySeason", () => {
  it("groups the episodes by season, with seasons and episodes in ascending order", () => {
    const groups = bySeason([episodeOf({ season: 2, episode: 1 }), episodeOf({ episode: 3 }), episodeOf({ episode: 2 })])
    expect(groups.map((group) => group.season)).toEqual([1, 2])
    expect(groups[0].episodes.map((view) => view.episode)).toEqual([2, 3])
    expect(groups[1].episodes.map((view) => view.episode)).toEqual([1])
  })

  it("gives no seasons for no episodes", () => {
    expect(bySeason([])).toEqual([])
  })
})

describe("the choices of an episode", () => {
  // One episode with eight lines: a player under two voice-over labels, a line not yet playable, a compilation of
  // episodes 1-2, a line not obtained and a line with no height. A player's label is the first non-empty one of its lines.
  const lines = [
    row({ player_label: "Vibix", heights: [1080, 720] }),
    row({ voice_label: "AniStar", heights: [480] }),
    row({ player: "vk", state: "absent" }),
    row({ source_id: 8, player: "kodik" }),
    row({ source_id: 8, player: "kodik", player_label: "Kodik", voice_label: "AniStar", heights: [720, 1080] }),
    row({ episode_end: 2, heights: [2160] }),
    row({ source_id: 8, player: "kodik", state: "not_obtained", heights: [2160] }),
    row({ source_id: 9, player: "astar", heights: [] }),
  ]
  const view = episodeOf({ rows: lines })

  it("makes a column of every voice-over label of the episode, alphabetically, with the variants of each by source and player", () => {
    expect(voiceColumns(view).map((column) => [column.group, column.variants.map((each) => `${each.sourceId}/${each.player}`)])).toEqual([
      ["AniStar", ["7/vibix", "8/kodik"]],
      ["Ruchime", ["7/vibix", "7/vk", "8/kodik", "9/astar"]],
    ])
  })

  it("gives a variant the highest step of its lines and says whether it plays and whether it can be the reference", () => {
    const columns = voiceColumns(view)
    const found = (group: string, sourceId: number, player: string) =>
      columns.find((column) => column.group === group)?.variants.find((each) => each.sourceId === sourceId && each.player === player)
    expect(found("AniStar", 7, "vibix")).toMatchObject({ height: 480, state: "referable", playerLabel: "Vibix" })
    expect(found("AniStar", 8, "kodik")).toMatchObject({ height: 1080, state: "referable", playerLabel: "Kodik" })
    // A compilation is no file of one episode: its step counts for the variant, but the variant is no reference by it alone.
    expect(found("Ruchime", 7, "vibix")).toMatchObject({ height: 2160, state: "referable" })
    expect(found("Ruchime", 7, "vk")).toMatchObject({ height: null, state: "absent" })
    expect(found("Ruchime", 9, "astar")).toMatchObject({ height: null, state: "referable" })
  })

  it("offers a variant that plays only as a compilation for the preference but not for the reference", () => {
    const only = episodeOf({ rows: [row({ episode_end: 2 })] })
    expect(voiceColumns(only)[0].variants[0]).toMatchObject({ state: "playable" })
  })

  it("makes no column for no lines", () => {
    expect(voiceColumns(episodeOf({ rows: [] }))).toEqual([])
  })
})

describe("the bodies of the commands", () => {
  it("sends the scope with the lever, its target and the value of the setting", () => {
    expect(setBody({ season: 2, episode: 5 }, "variant.allow", variantTarget("AniStar", 7, "vibix"), false)).toStrictEqual({
      season: 2, episode: 5, lever: "variant.allow", target: 'variant:["AniStar",7,"vibix"]', value: false,
    })
  })

  it("keeps a null target and leaves out the scope fields that are not set", () => {
    expect(setBody({}, "reference.choice", null, null)).toStrictEqual({ lever: "reference.choice", target: null, value: null })
    expect(setBody({ season: undefined, episode: 3 }, "voice.take", null, true))
      .toStrictEqual({ episode: 3, lever: "voice.take", target: null, value: true })
  })

  it("resets the scope and names a lever or a target only when they are given", () => {
    expect(resetBody({ season: 2 })).toStrictEqual({ season: 2 })
    expect(resetBody({ season: 2, episode: 5 }, "voice.take")).toStrictEqual({ season: 2, episode: 5, lever: "voice.take" })
    expect(resetBody({}, "voice.take", null)).toStrictEqual({ lever: "voice.take", target: null })
    expect(resetBody({ season: 1 }, undefined, undefined, "markup")).toStrictEqual({ season: 1, queue: "markup" })
  })
})

describe("isDeviation", () => {
  const settled = episodeOf({ rows: [row()], references: [ref(1, 1)], dubs: [dub(1, 1)] })

  it("is false when the references answer every line and nothing departs from the norm", () => {
    expect(isDeviation(settled)).toBe(false)
    expect(isDeviation(episodeOf())).toBe(false)
  })

  it("is true when lines have no reference at all", () => {
    expect(isDeviation(episodeOf({ rows: [row()] }))).toBe(true)
  })

  it("is true for a reference that is not taken from its level", () => {
    for (const mark of ["own", "other_player", "none", "unfit"] as const) {
      expect(isDeviation(episodeOf({ rows: [row()], references: [ref(1, 1, { mark })] }))).toBe(true)
    }
  })

  it("is true for a lever set on the episode itself, and not for one set on the season", () => {
    expect(isDeviation(episodeOf({ levers: [lever("voice.take", null, false, "episode")] }))).toBe(true)
    expect(isDeviation(episodeOf({ levers: [lever("voice.take", null, false, "season")] }))).toBe(false)
  })

  it("is true for a voice-over in error, and not for one still running", () => {
    expect(isDeviation(episodeOf({ dubs: [dub(1, 1, "error")] }))).toBe(true)
    expect(isDeviation(episodeOf({ dubs: [dub(1, 1, "running")] }))).toBe(false)
  })
})

describe("isOff", () => {
  const switched = episodeOf({ rows: [row()], references: [ref(1, 1, { mark: "off", source_id: null })] })

  it("is true when every slot of the episode is off, and not when one of its parts is taken or it has no reference", () => {
    expect(isOff(switched)).toBe(true)
    const parts = [ref(1, 1, { part: 1, mark: "off" }), ref(1, 1, { part: 2 })]
    expect(isOff(episodeOf({ rows: [row()], references: parts }))).toBe(false)
    expect(isOff(episodeOf({ rows: [row()] }))).toBe(false)
  })

  it("is not a departure from the norm: the user chose it", () => {
    expect(isDeviation(switched)).toBe(false)
  })

  it("tells a whole level switched off: it has episodes and every one is off", () => {
    expect(allOff([switched, episodeOf({ references: [ref(1, 2, { mark: "off" })] })])).toBe(true)
    expect(allOff([switched, episodeOf({ references: [ref(1, 2)] })])).toBe(false)
    expect(allOff([])).toBe(false)
  })
})

describe("fileNamesOf", () => {
  it("gives the names of the files of one episode, the whole one first, each once, and none for another episode", () => {
    const rows = [
      { season: 1, episode: 2, part: 2, name: "b" }, { season: 1, episode: 2, part: null, name: "a" },
      { season: 1, episode: 2, part: 1, name: "b" }, { season: 1, episode: 3, part: null, name: "c" },
      { season: 2, episode: 2, part: null, name: "d" }, { season: 1, episode: 2, part: null, name: null },
    ]
    expect(fileNamesOf(rows, 1, 2)).toEqual(["a", "b"])
    expect(fileNamesOf(rows, 9, 9)).toEqual([])
  })
})

describe("referenceOf", () => {
  const view = episodeOf({ references: [ref(1, 2, { part: null }), ref(1, 2, { part: 2 })] })

  it("finds the reference of a part, and of the whole episode when no part is named", () => {
    expect(referenceOf(view)?.part).toBeNull()
    expect(referenceOf(view, null)?.part).toBeNull()
    expect(referenceOf(view, 2)?.part).toBe(2)
  })

  it("gives null for a part the episode does not have", () => {
    expect(referenceOf(view, 3)).toBeNull()
    expect(referenceOf(episodeOf(), null)).toBeNull()
  })

  it("treats a reference without a part field as the whole episode", () => {
    expect(referenceOf(episodeOf({ references: [ref(1, 4)] }))).toEqual(ref(1, 4))
  })
})

describe("targets", () => {
  it("reads back the group, the source and the player a variant target was made from", () => {
    expect(parseVariantTarget(variantTarget("AniStar", 7, "vibix"))).toEqual({ group: "AniStar", sourceId: 7, player: "vibix" })
    expect(parseVariantTarget(variantTarget("A / B", 0, "a/b:c"))).toEqual({ group: "A / B", sourceId: 0, player: "a/b:c" })
    expect(parseVariantTarget(variantTarget('Q"uote', 1, "variant:x"))).toEqual({ group: 'Q"uote', sourceId: 1, player: "variant:x" })
  })

  it("makes the target as the core does: a compact JSON array", () => {
    expect(variantTarget("Ruchime", 3, "vibix")).toBe('variant:["Ruchime",3,"vibix"]')
    expect(variantTarget("Ё", 2, "я")).toBe('variant:["Ё",2,"я"]')
  })

  it("reads back the group a group target was made from, a name with its own colon included", () => {
    expect(parseGroupTarget(groupTarget("AniStar"))).toBe("AniStar")
    expect(parseGroupTarget(groupTarget("group:x"))).toBe("group:x")
    expect(parseGroupTarget(groupTarget("Studio Isaev"))).toBe("Studio Isaev")
  })

  it("gives null for any text of another kind", () => {
    expect(parseVariantTarget("group:AniStar")).toBeNull()
    expect(parseVariantTarget("variant:")).toBeNull()
    expect(parseVariantTarget('variant:["Ruchime",3]')).toBeNull()
    expect(parseVariantTarget('variant:["Ruchime","3","vk"]')).toBeNull()
    expect(parseVariantTarget('variant: ["Ruchime", 3, "vk"]')).toBeNull()
    expect(parseVariantTarget("")).toBeNull()
    expect(parseGroupTarget("variant:[\"Ruchime\",3,\"vk\"]")).toBeNull()
    expect(parseGroupTarget("")).toBeNull()
  })
})

describe("outputLines", () => {
  const ref1 = (over: Partial<Reference> = {}): Reference => ref(1, 1, { source_id: 7, row_key: "a", player: "vk", voice_group: "ПВА ШОУ", height: 2160, ...over })
  const dubOn = (label: string, over: Partial<VoiceDub> = {}): VoiceDub => ({ ...dub(1, 1), voice_key: label, voice_label: label, source_id: 9, player: "kodik", ...over })

  it("puts the reference first and then each voice-over, each with the player it comes from and the best height of that variant", () => {
    const rows = [
      row({ source_id: 7, player: "vk", player_label: "VK", voice_label: "ПВА ШОУ", heights: [1080, 2160] }),
      row({ row_key: "k", source_id: 9, player: "kodik", player_label: "Kodik", voice_label: "AniStar", heights: [360, 720] }),
    ]
    const lines = outputLines(episodeOf({ rows, references: [ref1()], dubs: [dubOn("AniStar", { state: "downloading" })] }))
    expect(lines.map((line) => [line.kind, line.group, line.playerLabel, line.height])).toEqual([
      ["reference", "ПВА ШОУ", "VK", 2160], ["dub", "AniStar", "Kodik", 720],
    ])
  })

  it("reads the status of a reference from the file of its row, and of a voice-over from its own state", () => {
    const files = [{ unique_id: "u", source_id: 7, row_key: "a", status: "completed" }, { unique_id: "v", source_id: 7, row_key: "b", status: "pending" }]
    const lines = outputLines(episodeOf({ references: [ref1()], files, dubs: [dubOn("AniStar", { state: "align_queued" })] }))
    expect([lines[0].file, lines[0].dub, lines[1].file, lines[1].dub?.state]).toEqual(["completed", null, null, "align_queued"])
    expect(outputLines(episodeOf({ references: [ref1()], files: [] }))[0].file).toBeNull()
  })

  it("names the player of a voice-over by the label of its source's table, though the episode has no line of that player", () => {
    const [line] = outputLines(episodeOf({ dubs: [dubOn("AniStar")], labels: { "9:kodik": "Kodik" } }))
    expect(line.playerLabel).toBe("Kodik")
  })

  it("makes no line for a reference with no source, and names no player for a voice-over the core names none for", () => {
    const none = ref1({ mark: "none", source_id: null, row_key: null, player: null, voice_group: null, height: null })
    expect(outputLines(episodeOf({ references: [none] }))).toEqual([])
    const [line] = outputLines(episodeOf({ dubs: [dubOn("AniStar", { source_id: null, player: null })] }))
    expect([line.player, line.playerLabel, line.height]).toEqual([null, null, null])
  })
})

describe("perRowOf", () => {
  it("takes as many cards a row as the tracks allow, narrowed by the number of episodes, never more than fit", () => {
    // places: 6 of the smallest cards fit; then the tracks (5, 12, 18, 30) and the episodes (12, 18, 40, 655).
    expect([5, 12, 18, 30].map((tracks) => perRowOf(6, tracks, 655))).toEqual([6, 3, 2, 1])
    expect([12, 18, 24, 40, 48, 49].map((episodes) => perRowOf(6, 5, episodes))).toEqual([1, 2, 2, 4, 4, 6])
    expect(perRowOf(6, 12, 24)).toBe(2) // the episodes ask for 2, the tracks allow 3
    expect(perRowOf(6, 12, 100)).toBe(3) // the episodes ask for more, the tracks allow 3
    expect([1, 2, 3, 4].map((places) => perRowOf(places, 12, 655))).toEqual([1, 1, 1, 2]) // a narrow window
    expect(perRowOf(0, 5, 655)).toBe(1)
  })
})

describe("the strip and the state of an episode", () => {
  const ref1: Reference = ref(1, 1, { source_id: 7, row_key: "a", player: "vk", voice_group: "A" })
  const dubOf = (label: string, state: VoiceDub["state"]): VoiceDub => ({ ...dub(1, 1, state), voice_key: label, voice_label: label })
  const view = (files: string, ...dubs: VoiceDub[]) => episodeOf({
    references: [ref1], dubs, files: [{ unique_id: "u", source_id: 7, row_key: "a", status: files }],
    rows: [row({ source_id: 7, row_key: "a", player: "vk", voice_label: "A" })],
  })

  it("gives a cell per track in the order of the container: the reference by its file, a voice-over by its state", () => {
    const cells = trackCells(view("completed", dubOf("B", "running"), dubOf("C", "error")), ["C", "A", "B", "D"])
    expect(cells.map((cell) => [cell.pos, cell.group, cell.state, cell.isReference])).toEqual([
      [1, "C", "error", false], [2, "A", "done", true], [3, "B", "align", false], [4, "D", "none", false]])
  })

  it("takes the own sound of a cut video for the picture's voice-over, the sound of its timeline being a track", () => {
    const cut = episodeOf({
      references: [{ ...ref1, voice_group: "Оригинал", markup: true,
                     picture: { source_id: 8, player: "vk", height: 2160, voice_group: "ПВА" } }],
      dubs: [dubOf("B", "done"), dubOf("Оригинал", "done")],
      files: [{ unique_id: "u", source_id: 7, row_key: "a", status: "completed" }],
    })
    expect(trackCells(cut, ["ПВА", "B", "Оригинал"]).map((cell) => [cell.group, cell.state, cell.isReference])).toEqual([
      ["ПВА", "done", true], ["B", "done", false], ["Оригинал", "done", false]])
  })

  it("colours an episode by the worst of its tracks, and by its switch", () => {
    const status = (each: EpisodeView, order: string[]) => episodeStatus(each, trackCells(each, order))
    expect(status(view("completed", dubOf("B", "done")), ["A", "B"])).toBe("ready")
    expect(status(view("downloading", dubOf("B", "done")), ["A", "B"])).toBe("down")
    expect(status(view("completed", dubOf("B", "align_queued")), ["A", "B"])).toBe("align")
    expect(status(view("pending", dubOf("B", "planned")), ["A", "B"])).toBe("wait")
    expect(status(view("completed", dubOf("B", "error")), ["A", "B"])).toBe("error")
    // A voice-over no source has is no error: the episode is put together in part.
    expect(status(view("completed", dubOf("B", "absent"), dubOf("C", "done")), ["A", "B", "C"])).toBe("partial")
    expect(status(view("completed", dubOf("B", "absent"), dubOf("C", "planned")), ["A", "B", "C"])).toBe("wait")
    // A voice-over the user switched off is no part of the episode: it is ready without it, not partial.
    expect(status(view("completed", dubOf("B", "off"), dubOf("C", "done")), ["A", "B", "C"])).toBe("ready")
    expect(status(episodeOf({ references: [ref(1, 1, { mark: "none" })] }), [])).toBe("error")
    expect(status(episodeOf({ references: [ref(1, 1, { mark: "off" })] }), [])).toBe("off")
  })
})

describe("the marks of the sources", () => {
  it("numbers the sources by their place in the series and keeps the colour each was given", () => {
    const marks = sourceMarks([
      { id: 40, position: 3, color: 7, url: "https://animakima.ru/pages/x/" },
      { id: 33, position: 0, color: 0, url: "https://dunhuatv.ru/761.html" },
      { id: 38, position: 1, color: 2, url: "https://vkvideo.ru/@mir.donghua|Вечная воля" },
    ])
    expect(marks).toEqual({
      33: { n: 1, color: 0, place: "dunhuatv.ru" }, 38: { n: 2, color: 2, place: "vkvideo.ru @mir.donghua" },
      40: { n: 3, color: 7, place: "animakima.ru" },
    })
  })
})

describe("the levers of a queue", () => {
  it("finds them by their description, not by their names", () => {
    expect(queueLevers(SPECS, "reference")).toEqual({ order: "reference.order", allow: "reference.allow", take: undefined })
    expect(queueLevers(SPECS, "voice")).toEqual({ order: "voice.order", allow: "voice.allow", take: "voice.take" })
    expect(queueLevers([], "voice")).toEqual({ order: undefined, allow: undefined, take: undefined })
    expect(queueLevers(SPECS, "markup")).toEqual({ order: "markup.order", allow: "markup.allow", take: undefined })
  })

  it("makes the value of an order from the rows in the order the user put them", () => {
    const rows: QueueRow[] = [
      { group: "A", source_id: 2, player: "kodik", player_label: "Kodik", episodes: "1", stale: "", allowed: true, allowed_level: "default" },
      { group: "B", source_id: 1, player: "vk", player_label: "VK", episodes: "1", stale: "", allowed: false, allowed_level: "season" },
    ]
    expect(orderValue("reference", rows)).toEqual([
      { group: "A", source_id: 2, player: "kodik" }, { group: "B", source_id: 1, player: "vk" }])
    expect(orderValue("voice", rows)).toEqual([{ source_id: 2, player: "kodik" }, { source_id: 1, player: "vk" }])
    expect(orderValue("markup", rows)).toEqual(orderValue("reference", rows))
  })
})

describe("spansOf", () => {
  it("words a set of episodes as runs", () => {
    expect(spansOf([3, 1, 2, 5, 7, 6, 2])).toBe("1–3, 5–7")
    expect(spansOf([4])).toBe("4")
    expect(spansOf([])).toBe("")
  })
})
