import { describe, expect, it } from "vitest"
import { fileOf, heldFile, planCuts, planDubs, planFiles, seasonVoiceOrders, takenReference, trackOf } from "./plan"
import { liveFrom } from "./live"
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

  it("shows a voice-over as its work is at while the sync runs: only planned where no work names it, and when the sync does not run", () => {
    const tracks = [goalTrack("A", "download", rowRef(8, "a")), goalTrack("B", "realign", rowRef(8, "b")), goalTrack("C", "download", rowRef(8, "c"))]
    const view = planView({ episodes: [goal([1, 1], goalVideo("download", rowRef(7, "r1"), "S01E01.mp4"), tracks)] })
    const live = liveFrom([["dub", "1:1:A", "downloading"], ["dub", "1:1:B", "running"]])
    expect(planDubs({ ...view, running: true }, {}, live).map((dub) => dub.state)).toEqual(["downloading", "running", "planned"])
    expect(planDubs({ ...view, running: true }).map((dub) => dub.state)).toEqual(["planned", "planned", "planned"])
    expect(planDubs(view, {}, live).map((dub) => dub.state)).toEqual(["planned", "planned", "planned"])
  })

  it("makes a voice-over an error when its download or its alignment failed for its row, and not for another row", () => {
    const tracks = [goalTrack("A", "download", rowRef(8, "a")), goalTrack("B", "realign", rowRef(8, "b")),
      goalTrack("C", "realign", rowRef(8, "c"))]
    const view = planView({
      failures: [{ kind: "download", row: rowRef(8, "a"), video: null, voice: null, covered: false }, { kind: "align", row: rowRef(8, "b"), video: null, voice: null, covered: false }],
      episodes: [goal([1, 1], goalVideo("download", rowRef(7, "r1"), "S01E01.mp4"), tracks)],
    })
    expect(planDubs(view).map((dub) => dub.state)).toEqual(["error", "error", "planned"])
  })

  it("names a voice-over the user switched off for the episode off, even with a failure of it, and absent elsewhere", () => {
    const view = planView({
      failures: [{ kind: "align", row: rowRef(8, "b"), video: null, voice: "B", episodes: [[1, 1]], covered: false }],
      episodes: [{ ...goal([1, 1], goalVideo("asset", rowRef(7, "r1"), "S01E01.mp4")), voices_off: ["B"] },
        goal([1, 2], goalVideo("asset", rowRef(7, "r2"), "S01E02.mp4"))],
    })
    expect(planDubs(view, { 1: ["B"] }).map((dub) => [dub.episode, dub.state])).toEqual([[1, "off"], [2, "absent"]])
  })

  it("makes a voice-over the plan passes over for its failed attempt an error of the episodes of its row, not absent", () => {
    const view = planView({
      failures: [{ kind: "align", row: rowRef(8, "b"), video: null, voice: "B", episodes: [[1, 1]], covered: false }],
      episodes: [goal([1, 1], goalVideo("asset", rowRef(7, "r1"), "S01E01.mp4")), goal([1, 2], goalVideo("asset", rowRef(7, "r2"), "S01E02.mp4"))],
    })
    expect(planDubs(view, { 1: ["B"] }).map((dub) => [dub.episode, dub.state, dub.source_id])).toEqual([[1, "error", 8], [2, "absent", null]])
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
      unique_id: "1:2", source_id: 7, row_key: "r2", status: "completed", cut: null, pending_cut: null, online: true,
    }])
  })

  it("tells a held video whose row the source forgot as offline, and a video to download as online", () => {
    const view = planView({
      assets: [{ ...asset("S01E01.mp4", rowRef(7, "r1")), video: { ...asset("S01E01.mp4", rowRef(7, "r1")).video, online: false } }],
      episodes: [goal([1, 1], goalVideo("asset", rowRef(7, "r1"), "S01E01.mp4")), goal([1, 2], goalVideo("download", rowRef(7, "r2"), null))],
    })
    expect(planFiles(view).map((file) => file.online)).toEqual([false, true])
  })

  it("makes a download that failed an error, one under way downloading while the sync runs, and one not reached pending", () => {
    const view = planView({
      failures: [{ kind: "download", row: rowRef(7, "r1"), video: null, voice: null, covered: false }],
      episodes: [
        goal([1, 1], goalVideo("download", rowRef(7, "r1"), null)),
        goal([1, 2], goalVideo("download", rowRef(7, "r2"), null)),
        goal([1, 3], goalVideo("download", rowRef(7, "r3"), null)),
      ],
    })
    expect(planFiles(view).map((file) => file.status)).toEqual(["error", "pending", "pending"])
    const live = liveFrom([["video", "1:1", "downloading"], ["video", "1:2", "queued"]])
    expect(planFiles({ ...view, failures: [], running: true }, undefined, live).map((file) => file.status)).toEqual(["downloading", "queued", "pending"])
    expect(planFiles({ ...view, failures: [], running: true }).map((file) => file.status)).toEqual(["pending", "pending", "pending"])
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
    const failed = planCuts({ ...view, failures: [{ kind: "cut", row: rowRef(9, "c"), video: rowRef(9, "c"), voice: null, covered: false }] })
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
    expect(trackOf(view, "S01E02.mp4", "AniStar")?.row).toEqual(rowRef(8, "k1"))
    expect(trackOf(view, "S01E02.mp4", "Студия")?.row).toEqual(rowRef(7, "r2"))
    expect(trackOf(view, "S01E02.mp4", "Nobody")).toBeNull()
    expect(trackOf(view, "S01E09.mp4", "AniStar")).toBeNull()
    expect(trackOf(undefined, "S01E02.mp4", "AniStar")).toBeNull()
  })
})

describe("seasonVoiceOrders", () => {
  it("keeps the order of the layout and puts after it the voice-overs the files hold that no source gives any more", () => {
    const view = planView({ episodes: [
      goal([1, 1], { ...goalVideo("asset", rowRef(1, "r1"), "a.mkv"), group: "Gone" }, [goalTrack("B", "reuse", rowRef(2, "b1"))]),
      goal([1, 2], { ...goalVideo("asset", rowRef(1, "r2"), "b.mkv"), group: "Gone" }, [goalTrack("Old", "reuse", rowRef(3, "o2"))]),
      goal([2, 1], goalVideo("download", rowRef(4, "n1"), null), [goalTrack("B", "download", rowRef(2, "b9"))]),
    ] })
    expect(seasonVoiceOrders([{ season: 1, voices: [{ label: "B", episodes: "1–2" }] }], view)).toEqual([
      { season: 1, voices: [{ label: "B", episodes: "1–2", online: true }, { label: "Gone", episodes: "1–2", online: false },
        { label: "Old", episodes: "2", online: false }] },
      { season: 2, voices: [{ label: "B", episodes: "1", online: false }] },
    ])
    expect(seasonVoiceOrders([{ season: 1, voices: [{ label: "B", episodes: "1" }] }], undefined))
      .toEqual([{ season: 1, voices: [{ label: "B", episodes: "1", online: true }] }])
  })

  it("shows a voice-over of the order with no row in the place it takes when the files hold it, and not when they do not", () => {
    const view = planView({ episodes: [goal([1, 1], goalVideo("asset", rowRef(1, "r1"), "a.mkv"), [goalTrack("Kept", "reuse", rowRef(2, "k1"))])] })
    const order = [{ season: 1, voices: [{ label: "A", episodes: "1" }, { label: "Kept", episodes: "" }, { label: "Never", episodes: "" }] }]
    expect(seasonVoiceOrders(order, view)).toEqual([{ season: 1, voices: [{ label: "A", episodes: "1", online: true },
      { label: "Kept", episodes: "1", online: false }] }])
    expect(seasonVoiceOrders([{ season: 2, voices: [{ label: "Never", episodes: "" }] }], undefined)).toEqual([])
  })

  it("puts the voice-over of the markup reference last, out of the order of its season, as the files hold it", () => {
    const view = planView({ episodes: [{ ...goal([1, 1], goalVideo("cut", rowRef(3, "o1"), null),
      [goalTrack("A", "download", rowRef(2, "a1")), goalTrack("Оригинал", "timeline", rowRef(3, "o1"))]), markup: "Оригинал" }] })
    const order = [{ season: 1, voices: [{ label: "Оригинал", episodes: "1" }, { label: "ПВА", episodes: "1" }, { label: "A", episodes: "1" }] }]
    expect(seasonVoiceOrders(order, view)[0].voices.map((voice) => voice.label)).toEqual(["ПВА", "A", "Оригинал"])
    expect(seasonVoiceOrders(order, undefined)[0].voices.map((voice) => voice.label)).toEqual(["Оригинал", "ПВА", "A"])
  })

  it("gives each voice-over the user's name of its track, in every season, the offline ones too", () => {
    const view = planView({ episodes: [goal([2, 1], goalVideo("asset", rowRef(1, "r1"), "a.mkv"), [goalTrack("Kept", "reuse", rowRef(2, "k1"))])] })
    const titles = { A: "ПМ_A", Kept: "Старая" }
    expect(seasonVoiceOrders([{ season: 1, voices: [{ label: "A", episodes: "1" }, { label: "B", episodes: "1" }] }], view, titles))
      .toEqual([
        { season: 1, voices: [{ label: "A", episodes: "1", online: true, title: "ПМ_A" }, { label: "B", episodes: "1", online: true }] },
        { season: 2, voices: [{ label: "Kept", episodes: "1", online: false, title: "Старая" }] },
      ])
  })
})

describe("the work that failed for the moment in the last sync", () => {
  it("makes the voice-over of its alignment an error of its episode, with the reason, and the video of a failed download one too", () => {
    const view = planView({
      problems: [{ slot: [1, 1], kind: "align", voice: "B", reason: "CUDA out of memory" },
        { slot: [1, 2], kind: "download_video", voice: null, reason: "обрыв" }],
      episodes: [goal([1, 1], goalVideo("asset", rowRef(7, "r1"), "a.mkv"), [goalTrack("B", "download", rowRef(8, "b"))]),
        goal([1, 2], goalVideo("download", rowRef(7, "r2"), null), [goalTrack("B", "download", rowRef(8, "c"))])],
    })
    const dubs = planDubs(view)
    expect(dubs.map((dub) => [dub.episode, dub.state])).toEqual([[1, "error"], [2, "planned"]])
    expect(dubs[0].detail).toEqual({ code: "plan_passing", params: { reason: "CUDA out of memory" } })
    expect(planFiles(view).map((file) => file.status)).toEqual(["completed", "error"])
  })

  it("is work under way again while a sync runs, as its work says", () => {
    const view = planView({ running: true, problems: [{ slot: [1, 1], kind: "align", voice: "B", reason: "x" }],
      episodes: [goal([1, 1], goalVideo("asset", rowRef(7, "r1"), "a.mkv"), [goalTrack("B", "realign", rowRef(8, "b"))])] })
    expect(planDubs(view, {}, liveFrom([["dub", "1:1:B", "running"]])).map((dub) => dub.state)).toEqual(["running"])
    expect(planDubs(view).map((dub) => dub.state)).toEqual(["planned"])
  })
})

describe("heldFile", () => {
  it("is what the file of the episode holds — its video and each voice-over by source and player — and nothing for an episode with no file", () => {
    const view = planView({
      assets: [asset("S01E01.mp4", rowRef(7, "r1"), [assetTrack("ПВА ШОУ", rowRef(90, "kodik:1"), true, { player: "kodik" })], null, [1, 1],
        { player: "line:bfzym3u8", group: "Китайский" })],
      episodes: [goal([1, 1], goalVideo("asset", rowRef(7, "r1"), "S01E01.mp4"),
        [goalTrack("ПВА ШОУ", "download", rowRef(90, "cvh:1"), { player: "cvh" })])],
    })
    // The plan wants the voice-over from cvh; the file holds the one of kodik, and the own sound of the video is its group's.
    expect(heldFile(view, 1, 1)).toEqual({
      video: { source_id: 7, player: "line:bfzym3u8" },
      tracks: { "Китайский": { source_id: 7, player: "line:bfzym3u8" }, "ПВА ШОУ": { source_id: 90, player: "kodik" } },
    })
    expect(heldFile(view, 1, 2)).toBeNull()
    expect(heldFile(undefined, 1, 1)).toBeNull()
  })

  it("takes the compilation of a cut file as the source of its video, and passes over a file that is switched off", () => {
    const view = planView({ assets: [
      asset("S01E02.mp4", rowRef(7, "pic2"), [], rowRef(4, "comp"), [1, 2], { player: "vk" }),
      { ...asset("S01E03.mp4", rowRef(7, "r3"), [], null, [1, 3]), off: true }] })
    expect(heldFile(view, 1, 2)?.video).toEqual({ source_id: 4, player: "vk" })
    expect(heldFile(view, 1, 3)).toBeNull()
  })
})

describe("takenReference", () => {
  const reference = (over: object) => ({ season: 1, episode: 1, part: null, mark: "by_level" as const, source_id: null, row_key: null,
    player: null, voice_group: null, height: null, reason: null, compilation: null, picture: null, markup: false, ...over })

  it("is the variant the plan takes for the episode — what the order of the queue only wishes, as against the fact", () => {
    const view = planView({ references: [reference({ source_id: 7, player: "line:duanju" }), reference({ episode: 2, source_id: 5, player: "vk" })] })
    expect(takenReference(view, 1, 1)).toEqual({ source_id: 7, player: "line:duanju" })
    expect(takenReference(view, 1, 2)).toEqual({ source_id: 5, player: "vk" })
  })

  it("is none for an episode the plan takes no video for, a part nobody asked for, or no plan", () => {
    const view = planView({ references: [reference({ mark: "none" }), reference({ episode: 3, part: 2, source_id: 7, player: "vk" })] })
    expect(takenReference(view, 1, 1)).toBeNull()
    expect(takenReference(view, 1, 3)).toBeNull()
    expect(takenReference(view, 1, 3, 2)).toEqual({ source_id: 7, player: "vk" })
    expect(takenReference(undefined, 1, 1)).toBeNull()
  })
})
