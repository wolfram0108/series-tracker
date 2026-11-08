import { describe, expect, it } from "vitest"
import { fileOf, planCuts, planDubs, planFiles, trackOf } from "./plan"
import { asset, assetTrack, goal, goalTrack, goalVideo, planView, rowRef } from "./plan.fixtures"

describe("planDubs", () => {
  it("names absent a voice-over of the order of the season the plan cannot get for the episode", () => {
    const view = planView({
      episodes: [goal([1, 1], { ...goalVideo("asset", rowRef(7, "r1"), "S01E01.mp4"), group: "HORIZON" },
                      [goalTrack("AniStar", "reuse", rowRef(8, "a1"))])],
    })
    const dubs = planDubs(view, { 1: ["HORIZON", "AniStar", "AnimeVost"] })
    // The reference's own voice is the video's sound, not a track: it is neither a voice-over nor absent.
    expect(dubs.map((dub) => [dub.voice_label, dub.state])).toEqual([["AniStar", "done"], ["AnimeVost", "absent"]])
  })

  it("gives a voice-over the file holds as done, with the player of its row and whether the source gives it now", () => {
    const row = rowRef(8, "k1")
    const view = planView({
      assets: [asset("S01E01.mp4", rowRef(7, "r1"), [assetTrack("AniStar", row, true, { player: "kodik" })])],
      episodes: [goal([1, 1], goalVideo("asset", rowRef(7, "r1"), "S01E01.mp4"),
        [goalTrack("AniStar", "reuse", row, { player: "kodik" })])],
    })
    expect(planDubs(view)).toEqual([{
      season: 1, episode: 1, part: null, voice_key: "AniStar", voice_label: "AniStar", state: "done",
      source_id: 8, player: "kodik", online: true,
    }])
  })

  it("shows a download as downloading and an alignment as running while the sync runs, and as planned when it does not", () => {
    const tracks = [goalTrack("A", "download", rowRef(8, "a")), goalTrack("B", "realign", rowRef(8, "b"))]
    const view = planView({ episodes: [goal([1, 1], goalVideo("download", rowRef(7, "r1"), "S01E01.mp4"), tracks)] })
    expect(planDubs({ ...view, running: true }).map((dub) => dub.state)).toEqual(["downloading", "running"])
    expect(planDubs(view).map((dub) => dub.state)).toEqual(["planned", "planned"])
  })

  it("makes a voice-over an error when its download or its alignment failed for its row, and not for another row", () => {
    const tracks = [goalTrack("A", "download", rowRef(8, "a")), goalTrack("B", "realign", rowRef(8, "b")),
      goalTrack("C", "realign", rowRef(8, "c"))]
    const view = planView({
      failures: [{ kind: "download", row: rowRef(8, "a"), video: null }, { kind: "align", row: rowRef(8, "b"), video: null }],
      episodes: [goal([1, 1], goalVideo("download", rowRef(7, "r1"), "S01E01.mp4"), tracks)],
    })
    expect(planDubs(view).map((dub) => dub.state)).toEqual(["error", "error", "planned"])
  })

  it("takes online from the asset's track when the file holds it, from a download when it does not, and leaves it unknown otherwise", () => {
    const view = planView({
      assets: [asset("S01E01.mp4", rowRef(7, "r1"), [assetTrack("A", rowRef(8, "a"), false)])],
      episodes: [goal([1, 1], goalVideo("asset", rowRef(7, "r1"), "S01E01.mp4"), [
        goalTrack("A", "reuse", rowRef(8, "a")),
        goalTrack("B", "download", rowRef(8, "b")),
        goalTrack("C", "realign", rowRef(8, "c")),
      ])],
    })
    expect(planDubs(view).map((dub) => dub.online)).toEqual([false, true, null])
  })

  it("names the part of an episode of parts and gives nothing for no plan", () => {
    const view = planView({ episodes: [goal([1, 2, 3], goalVideo("download", rowRef(7, "r1"), null), [goalTrack("A", "download", rowRef(8, "a"))])] })
    expect(planDubs(view)[0]).toMatchObject({ season: 1, episode: 2, part: 3 })
    expect(planDubs(undefined)).toEqual([])
  })
})

describe("planFiles", () => {
  it("gives a file the plan holds as completed, with the row it came from and no cut", () => {
    const view = planView({
      assets: [asset("S01E02.mp4", rowRef(7, "r2"))],
      episodes: [goal([1, 2], goalVideo("asset", rowRef(7, "r2"), "S01E02.mp4"))],
    })
    expect(planFiles(view)).toEqual([{
      unique_id: "1:2", source_id: 7, row_key: "r2", status: "completed", cut: null, pending_cut: null,
    }])
  })

  it("makes a download that failed an error, one under way downloading while the sync runs, and one not reached pending", () => {
    const view = planView({
      failures: [{ kind: "download", row: rowRef(7, "r1"), video: null }],
      episodes: [
        goal([1, 1], goalVideo("download", rowRef(7, "r1"), null)),
        goal([1, 2], goalVideo("download", rowRef(7, "r2"), null)),
        goal([1, 3], goalVideo("download", rowRef(7, "r3"), null)),
      ],
    })
    expect(planFiles(view).map((file) => file.status)).toEqual(["error", "pending", "pending"])
    expect(planFiles({ ...view, failures: [], running: true }).map((file) => file.status)).toEqual(["downloading", "downloading", "downloading"])
  })

  it("carries the cut of a held file, and the cut the next build puts in as pending", () => {
    const cutOf = rowRef(9, "c")
    const compilation = rowRef(9, "c")
    const view = planView({
      assets: [asset("S01E01.mp4", rowRef(7, "r1"), [], cutOf)],
      episodes: [
        goal([1, 1], goalVideo("asset", rowRef(7, "r1"), "S01E01.mp4")),
        goal([1, 2], goalVideo("cut", rowRef(7, "r2"), null, compilation)),
      ],
    })
    // The layout names a compilation by its key: the plan's row of the compilation is told by it.
    const keyOf = (row: { source_id: number; row_key: string }) => `compilation:${row.source_id}/${row.row_key}`
    const [held, pending] = planFiles(view, keyOf)
    expect(held.cut).toEqual({ key: "compilation:9/c" })
    expect(pending.pending_cut).toEqual({ key: "compilation:9/c" })
    expect(pending.status).toBe("pending")
  })

  it("gives nothing for no plan", () => {
    expect(planFiles(undefined)).toEqual([])
  })
})

describe("planCuts", () => {
  it("shows a cut the plan has as planned, aligning while the sync runs, and failed when its cut failed", () => {
    const cut = goalVideo("cut", rowRef(9, "c"), null)
    const view = planView({ episodes: [goal([1, 1], cut), goal([1, 2], goalVideo("cut", rowRef(9, "d"), null))] })
    expect(planCuts(view).map((each) => each.status)).toEqual(["planned", "planned"])
    expect(planCuts({ ...view, running: true }).map((each) => each.status)).toEqual(["aligning", "aligning"])
    const failed = planCuts({ ...view, failures: [{ kind: "cut", row: rowRef(9, "c"), video: rowRef(9, "c") }] })
    expect(failed.map((each) => each.status)).toEqual(["failed", "planned"])
  })

  it("shows a held cut as done with the key of its compilation, and an episode of parts with its part", () => {
    const cutOf = rowRef(9, "c")
    const view = planView({
      assets: [asset("S01E01.mp4", rowRef(7, "r1"), [], cutOf), asset("S01E02.mp4", rowRef(7, "r2"))],
      episodes: [goal([1, 1], goalVideo("asset", rowRef(7, "r1"), "S01E01.mp4")),
        goal([1, 2, 2], goalVideo("asset", rowRef(7, "r2"), "S01E02.mp4"))],
    })
    expect(planCuts(view)).toEqual([{ season: 1, episode: 1, part: 0, cut_key: JSON.stringify(cutOf), status: "done" }])
    const parted = planView({
      assets: [asset("S01E02-2.mp4", rowRef(7, "r2"), [], cutOf)],
      episodes: [goal([1, 2, 2], goalVideo("asset", rowRef(7, "r2"), "S01E02-2.mp4"))],
    })
    expect(planCuts(parted)).toEqual([expect.objectContaining({ season: 1, episode: 2, part: 2, status: "done" })])
  })

  it("gives nothing for no plan", () => {
    expect(planCuts(undefined)).toEqual([])
  })
})

describe("fileOf and trackOf", () => {
  const view = planView({
    assets: [asset("S01E02.mp4", rowRef(7, "r2"), [
      assetTrack("AniStar", rowRef(8, "k1")),
      assetTrack("Ruchime", rowRef(7, "r2"), true, { group: null, label: "Студия" }),
    ])],
    episodes: [
      goal([1, 2], goalVideo("asset", rowRef(7, "r2"), "S01E02.mp4")),
      goal([1, 2, 1], goalVideo("download", rowRef(7, "r3"), "S01E02-1.mp4")),
    ],
  })

  it("finds the file of an episode, and of its part when a part is named, and nothing for another", () => {
    expect(fileOf(view, 1, 2, null)).toBe("S01E02.mp4")
    expect(fileOf(view, 1, 2, 1)).toBe("S01E02-1.mp4")
    expect(fileOf(view, 1, 2, 2)).toBeNull()
    expect(fileOf(view, 1, 3, null)).toBeNull()
    expect(fileOf(undefined, 1, 2, null)).toBeNull()
  })

  it("finds the row of a track of a file by the voice of its group or its label, and nothing for another", () => {
    expect(trackOf(view, "S01E02.mp4", "AniStar")).toEqual(rowRef(8, "k1"))
    expect(trackOf(view, "S01E02.mp4", "Студия")).toEqual(rowRef(7, "r2"))
    expect(trackOf(view, "S01E02.mp4", "Nobody")).toBeNull()
    expect(trackOf(view, "S01E09.mp4", "AniStar")).toBeNull()
    expect(trackOf(undefined, "S01E02.mp4", "AniStar")).toBeNull()
  })
})
