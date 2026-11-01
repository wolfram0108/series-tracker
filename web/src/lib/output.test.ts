import { describe, expect, it } from "vitest"
import type { components } from "../api/schema"
import type { VoiceDub } from "../stores/voices"
import {
  LEVER, bySeason, groupTarget, isDeviation, isOwn, joinEpisodes, leverState, parseGroupTarget, parsePlayerTarget,
  playerOptions, playerTarget, referenceOf, referenceOptions, resetBody, setBody, sourcePlayers, voiceGroups,
  type EpisodeView, type Lever, type LeverState, type Reference, type Row,
} from "./output"

type Schemas = components["schemas"]

// Samples have the shape of the schema: a row is one line of the layout, a reference is the answer for one episode.
const row = (over: Partial<Row> = {}): Row => ({
  series_id: 1, source_id: 7, row_key: "row", excluded: false, season: 1, episode_start: 1, episode_end: 1,
  voice_label: "Ruchime", voice_name: "Ruchime", player: "vibix", player_label: "", group: "", text: "",
  heights: [1080], state: "playable", link: "", ...over,
})
const ref = (season: number, episode: number, over: Partial<Reference> = {}): Reference =>
  ({ season, episode, mark: "by_level", ...over })
const lever = (name: string, target: string | null | undefined, value: unknown, level: Schemas["Level"]): Lever =>
  ({ lever: name, target, value, level })
const settings = (season: number, episode: number, levers: Lever[] = []): Schemas["EpisodeSettings"] =>
  ({ season, episode, levers })
const dub = (season: number, episode: number, state: VoiceDub["state"] = "done"): VoiceDub => ({
  season, episode, voice_key: "ruchime", voice_label: "Ruchime", status: "placed", state, hint: null, detail: null,
})
const episodeOf = (over: Partial<EpisodeView> = {}): EpisodeView => ({
  season: 1, episode: 1, references: [], rows: [], levers: [], dubs: [], ...over,
})

describe("joinEpisodes", () => {
  // A compilation of episodes 1-3, a single line of episode 2 from another source, an excluded line of episode 9,
  // a line with no season and a line with no number: only the first three lines make episodes.
  const compilation = row({ episode_start: 1, episode_end: 3 })
  const single = row({ source_id: 8, player: "kodik", player_label: "Kodik", episode_start: 2, episode_end: 2 })
  const excluded = row({ excluded: true, episode_start: 9, episode_end: 9 })
  const noSeason = row({ season: null, episode_start: 20, episode_end: 20 })
  const noNumber = row({ episode_start: null, episode_end: null })
  const lines = [compilation, single, excluded, noSeason, noNumber]

  it("joins a compilation to each of its episodes and a single line to its own", () => {
    const views = joinEpisodes(lines, [], [], [])
    expect(views.map((view) => [view.season, view.episode])).toEqual([[1, 1], [1, 2], [1, 3]])
    expect(views[0].rows).toEqual([compilation])
    expect(views[1].rows).toEqual([compilation, single])
    expect(views[2].rows).toEqual([compilation])
  })

  it("leaves out excluded lines and lines with no season or no number", () => {
    const numbers = joinEpisodes(lines, [], [], []).map((view) => view.episode)
    expect(numbers).not.toContain(9)
    expect(numbers).not.toContain(20)
  })

  it("keeps an episode that only the settings name, with its levers and no rows", () => {
    const levers = [lever(LEVER.take, groupTarget("AniStar"), false, "episode")]
    expect(joinEpisodes([], [], [settings(1, 5, levers)], [])).toEqual([episodeOf({ episode: 5, levers })])
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

  it("lists the source and player pairs of the episode, labelled from any of their lines", () => {
    expect(sourcePlayers(view)).toEqual([
      { sourceId: 7, player: "vibix", playerLabel: "Vibix" },
      { sourceId: 7, player: "vk", playerLabel: "vk" },
      { sourceId: 8, player: "kodik", playerLabel: "Kodik" },
      { sourceId: 9, player: "astar", playerLabel: "astar" },
    ])
  })

  it("lists the voice-over labels of the episode in alphabetical order", () => {
    expect(voiceGroups(view)).toEqual(["AniStar", "Ruchime"])
  })

  it("lists the players that carry one voice-over label, whatever the state of their lines", () => {
    expect(playerOptions(view, "AniStar")).toEqual([
      { sourceId: 7, player: "vibix", playerLabel: "Vibix" },
      { sourceId: 8, player: "kodik", playerLabel: "Kodik" },
    ])
    expect(playerOptions(view, "Ruchime").map((option) => option.player)).toEqual(["vibix", "vk", "kodik", "astar"])
    expect(playerOptions(view, "Missing")).toEqual([])
  })

  it("offers single playable lines by label and player, with the highest step of each", () => {
    expect(referenceOptions(view)).toEqual([
      { group: "AniStar", sourceId: 7, player: "vibix", playerLabel: "Vibix", height: 480 },
      { group: "AniStar", sourceId: 8, player: "kodik", playerLabel: "Kodik", height: 1080 },
      { group: "Ruchime", sourceId: 7, player: "vibix", playerLabel: "Vibix", height: 1080 },
      { group: "Ruchime", sourceId: 8, player: "kodik", playerLabel: "Kodik", height: 1080 },
      { group: "Ruchime", sourceId: 9, player: "astar", playerLabel: "astar", height: null },
    ])
  })

  it("offers no reference when no single line is playable", () => {
    expect(referenceOptions(episodeOf({ rows: [lines[2]] }))).toEqual([])
  })
})

describe("leverState", () => {
  const levers = [
    lever(LEVER.choice, playerTarget(7, "vibix"), 2, "series"),
    lever(LEVER.take, groupTarget("AniStar"), false, "episode"),
    lever(LEVER.allow, undefined, false, "season"),
  ]

  it("takes the value and the level of the record with this lever and target", () => {
    expect(leverState(levers, LEVER.choice, playerTarget(7, "vibix"))).toEqual({ value: 2, level: "series" })
    expect(leverState(levers, LEVER.take, groupTarget("AniStar"))).toEqual({ value: false, level: "episode" })
  })

  it("reads a record without a target as the target null", () => {
    expect(leverState(levers, LEVER.allow, null)).toEqual({ value: false, level: "season" })
    expect(leverState(levers, LEVER.allow, undefined)).toEqual({ value: false, level: "season" })
  })

  it("gives the default of the lever when no record sets it", () => {
    expect(leverState(levers, LEVER.choice, playerTarget(8, "kodik"))).toEqual({ value: null, level: "default" })
    expect(leverState(levers, LEVER.allow, groupTarget("Ruchime"))).toEqual({ value: true, level: "default" })
    expect(leverState(levers, LEVER.take, groupTarget("Ruchime"))).toEqual({ value: true, level: "default" })
    expect(leverState(levers, LEVER.player, null)).toEqual({ value: null, level: "default" })
  })

  it("gives null at the default level for a lever the registry does not know", () => {
    expect(leverState([], "unknown.lever", null)).toEqual({ value: null, level: "default" })
    expect(leverState([], "constructor", null)).toEqual({ value: null, level: "default" })
  })
})

describe("isOwn", () => {
  it("tells the level that sets a lever from the levels that only inherit it", () => {
    const state: LeverState = { value: 1, level: "season" }
    expect(isOwn(state, "season")).toBe(true)
    expect(isOwn(state, "series")).toBe(false)
    expect(isOwn(state, "episode")).toBe(false)
    expect(isOwn({ value: null, level: "default" }, "series")).toBe(false)
  })
})

describe("the bodies of the commands", () => {
  it("sends the scope with the lever, its target and the value of the setting", () => {
    expect(setBody({ season: 2, episode: 5 }, LEVER.choice, playerTarget(7, "vibix"), 2)).toStrictEqual({
      season: 2, episode: 5, lever: "reference.choice", target: "source:7/player:vibix", value: 2,
    })
  })

  it("keeps a null target and leaves out the scope fields that are not set", () => {
    expect(setBody({}, LEVER.allow, null, false)).toStrictEqual({ lever: "reference.allow", target: null, value: false })
    expect(setBody({ season: undefined, episode: 3 }, LEVER.take, null, true))
      .toStrictEqual({ episode: 3, lever: "voice.take", target: null, value: true })
  })

  it("resets the scope and names a lever or a target only when they are given", () => {
    expect(resetBody({ season: 2 })).toStrictEqual({ season: 2 })
    expect(resetBody({ season: 2, episode: 5 }, LEVER.take)).toStrictEqual({ season: 2, episode: 5, lever: "voice.take" })
    expect(resetBody({}, LEVER.take, null)).toStrictEqual({ lever: "voice.take", target: null })
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
    expect(isDeviation(episodeOf({ levers: [lever(LEVER.take, null, false, "episode")] }))).toBe(true)
    expect(isDeviation(episodeOf({ levers: [lever(LEVER.take, null, false, "season")] }))).toBe(false)
  })

  it("is true for a voice-over in error, and not for one still running", () => {
    expect(isDeviation(episodeOf({ dubs: [dub(1, 1, "error")] }))).toBe(true)
    expect(isDeviation(episodeOf({ dubs: [dub(1, 1, "running")] }))).toBe(false)
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
  it("reads back the source and the player a player target was made from", () => {
    expect(parsePlayerTarget(playerTarget(7, "vibix"))).toEqual({ sourceId: 7, player: "vibix" })
    expect(parsePlayerTarget(playerTarget(0, "a/b:c"))).toEqual({ sourceId: 0, player: "a/b:c" })
  })

  it("reads back the group a group target was made from, a name with its own colon included", () => {
    expect(parseGroupTarget(groupTarget("AniStar"))).toBe("AniStar")
    expect(parseGroupTarget(groupTarget("group:x"))).toBe("group:x")
    expect(parseGroupTarget(groupTarget("Studio Isaev"))).toBe("Studio Isaev")
  })

  it("gives null for any text of another kind", () => {
    expect(parsePlayerTarget("group:AniStar")).toBeNull()
    expect(parsePlayerTarget("source:7")).toBeNull()
    expect(parsePlayerTarget("source:007/player:vibix")).toBeNull()
    expect(parsePlayerTarget("source:x/player:vibix")).toBeNull()
    expect(parsePlayerTarget("")).toBeNull()
    expect(parseGroupTarget("source:7/player:vibix")).toBeNull()
    expect(parseGroupTarget("")).toBeNull()
  })
})
