import { describe, expect, it } from "vitest"
import { byEpisode, containerGroups, draftOf, episodeFolder, fileCounts, foldersOf, ruleOf, sourceState, streamLabel, type ContainerFile,
  type FileEntry, type ReleaseView, type TrackEntry } from "./torrent"
import { RELEASE_STATES } from "./states"

const kept = (state: ReleaseView["state"], done: number): ReleaseView => ({ url: "u", release: "r", title: "", state, watched: true,
  pending: false, files: [0, 1, 2].map((n) => ({ path: `${n}.mkv`, size: 1, progress: n < done ? 1 : 0.5, priority: n === 2 ? 0 : 1 })) })

describe("the state of a torrent source", () => {
  it("counts the files wanted of a download and an update, and the read of the files after it", () => {
    expect(sourceState(undefined, null, 0).stage).toBe("notLoaded")
    // A file half downloaded is half its bytes, whether or not another is whole: the bar moves with the bytes.
    expect(sourceState(kept("downloading", 1), null, 0)).toMatchObject({ stage: "download", progress: { done: 1, total: 2, share: 75 } })
    expect(sourceState(kept("updating", 2), null, 0)).toMatchObject({ stage: "update", progress: { done: 2, total: 2, share: 100 } })
    expect(sourceState(kept("ready", 2), { done: 1, total: 4 }, 3)).toMatchObject({ stage: "read", progress: { done: 1, total: 4, share: 25 } })
    expect(sourceState(kept("ready", 2), { done: 4, total: 4 }, 3)).toMatchObject({ stage: "ready", waiting: 3 })
    expect(sourceState({ ...kept("failed", 0), reason: "нет места" }, null, 0)).toMatchObject({ stage: "failed", reason: "нет места" })
  })

  it("has a state of the vocabulary for every stage", () => {
    expect(RELEASE_STATES).toEqual({ notLoaded: "notLoaded", download: "download", read: "unpack", ready: "done", update: "update",
      failed: "failed", archived: "archived" })
  })
})

const file = (path: string, kind: FileEntry["kind"], place: [number, number] | null, by: string | null = null, excluded = false): FileEntry => ({
  item: `r/${path}`, path, line: "", kind, size: 1, priority: 1, excluded, place: place && { season: place[0], episode: place[1], last: null }, by })

describe("the files of a release", () => {
  it("are counted by what they are and laid in seasons and episodes in the draft", () => {
    const files = [file("E02.mkv", "video", [1, 2], "Серия · 1"), file("E01.mkv", "video", [1, 1]), file("E01.mka", "audio", [1, 1]),
      file("OVA.mkv", "video", [0, 1], "manual"), file("menu.mkv", "video", null, null, true), file("x.mkv", "video", null),
      file("a.ttf", "font", null)]
    expect(fileCounts(files)).toEqual({ all: 7, unplaced: 1, placed: 4, excluded: 1, notInvolved: 1 })
    expect(draftOf(files).map((season) => [season.season, season.manual, season.episodes.map((each) => [each.episode, each.kinds])])).toEqual([
      [0, true, [[1, { video: 1 }]]], [1, false, [[1, { video: 1, audio: 1 }], [2, { video: 1 }]]]])
  })

  it("lie in the folders of their episodes, named as the files of the library are", () => {
    expect([episodeFolder(1, 5), episodeFolder(0, 12, 13), episodeFolder(2, 3, 3)]).toEqual(["s01e05", "s00e12-e13", "s02e03"])
  })

  it("name the rule that placed them, a row of a table by its number", () => {
    expect(ruleOf("Серия · 2")).toEqual({ rule: "Серия", row: 2 })
    expect(ruleOf("Исключить")).toEqual({ rule: "Исключить", row: null })
    expect(ruleOf("manual")).toBeNull()
    expect(ruleOf(null)).toBeNull()
  })
})

const track = (item: string, folder: string | null, episode: number, by: string | null = null): TrackEntry => ({
  item, line: "", kind: "audio", folder, by, excluded: false, waiting: !folder, language: "rus", subtitle_type: "", path: `${folder ?? "x"}/${item}`,
  place: { season: 1, episode, last: null }, stream: null })

describe("the tracks of a release", () => {
  it("are grouped by their episode and their folders say what they hold and the rule that named them", () => {
    const tracks = [track("b", "JAM", 2, "Озвучка · 1"), track("a", "JAM", 1, "Озвучка · 1"), track("c", null, 1), track("d", "JAM", 4)]
    expect(byEpisode(tracks).map((group) => [group.place?.episode, group.entries.map((each) => each.item)])).toEqual([
      [1, ["a", "c"]], [2, ["b"]], [4, ["d"]]])
    const folders = [{ kind: "voice" as const, name: "JAM", items: 9, manual: false, language: "rus", subtitle_type: "", fonts: [] },
      { kind: "subtitles" as const, name: "Cafe", items: 1, manual: false, language: "", subtitle_type: "", fonts: ["f.ttf"] }]
    expect(foldersOf(folders, tracks, "voice")).toEqual([{ ...folders[0], episodes: "1: 1–2, 4", rule: { rule: "Озвучка", row: 1 }, held: 3,
      fromMeta: false }])
    // A folder made of the titles of the files alone says so, and names no rule.
    const meta = [track("a", "AniLiberty", 1, "meta"), track("b", "AniLiberty", 2, "meta")]
    const [made] = foldersOf([{ ...folders[0], name: "AniLiberty" }], meta, "voice")
    expect([made.fromMeta, made.rule]).toEqual([true, null])
  })

  it("inside video files of one composition and one season are one container, a track standing for that track of each", () => {
    const streams = [{ index: 0, kind: "video" }, { index: 1, kind: "audio", language: "rus", title: "" }]
    const files: ContainerFile[] = [1, 2, 3].map((n) => ({ item: `r/E${n}.mkv`, path: `E${n}.mkv`, place: { season: n === 3 ? 2 : 1, episode: n, last: null }, streams }))
    const tracks = [1, 2, 3].map((n) => ({ ...track(`r/E${n}.mkv#1`, n === 1 ? "MS" : null, n) }))
    const groups = containerGroups(files, tracks)
    expect(groups.map((group) => [group.season, group.episodes, group.files])).toEqual([[2, "3", 1], [1, "1–2", 2]])
    expect(groups[1].streams.map((each) => [each.label, each.items, each.folder, each.unplaced])).toEqual([
      ["v0", [], null, false], ["a0", ["r/E1.mkv#1", "r/E2.mkv#1"], null, true]])
    expect(streamLabel({ index: 3, kind: "subtitle" }, 1)).toBe("s1")
  })
})
