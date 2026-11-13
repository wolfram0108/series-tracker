import { describe, expect, it } from "vitest"
import type { ConformResult, ConformTask, DownloadCard, PackCard, QueueTask, SlicingCard } from "../stores/queues"
import { t } from "../i18n"
import { PACK_STATES, capital, stateLabel, stateWord } from "./states"
import {
  conformWork, downloadWork, journalWork, monitorWork, packWork, processingWork, readWork, slicingWork, slotLabel,
} from "./work"

const cvh = { source_id: 55, source_n: 1, source_color: 0, place: "old.yummyani.me", player: "cvh", player_label: "CVH", height: 1080 }
const kodik = { ...cvh, player: "kodik", player_label: "Kodik", height: 720 }
const stages = (states: string[]) => ["queue", "link", "download", "remux", "check"].map((name, index) =>
  ({ name, state: states[index], seconds: 41 * (index + 1) })) as DownloadCard["stages"]
const download = (over: Partial<DownloadCard> = {}): DownloadCard => ({
  id: "88:s06e08:dub:Kazoku Project",
  need: { key: "88:s06e08:dub:Kazoku Project", series_id: 88, series_name: "ДжоДжо", sync_started: "2026-11-10T09:17:00+00:00",
          episode: "s06e08", kind: "dub", group: "Kazoku Project", voice_number: 15, cap: 720 },
  variant: kodik, status: "download", method: "ranged", stages: stages(["done", "done", "active", "skipped", "pending"]),
  log: [
    { kind: "attempt", variant: cvh, attempt: 3, of: 3, outcome: "failed", stage: "link", seconds: 126 },
    { kind: "replan", variant: kodik, attempt: 0, of: 0, outcome: "running", stage: null, seconds: 0 },
    { kind: "attempt", variant: kodik, attempt: 1, of: 3, outcome: "running", stage: null, seconds: 31 },
  ],
  figures: { progress: 47, done_mb: 312, total_mb: 664, speed: 10.4, eta: 34, source_seconds: 1452 },
  replanned: true, position: null, running: 0, limit: 0, queued_at: "2026-11-10T09:18:58+00:00", finished_at: "", seconds: 210,
  ...over,
} as DownloadCard)

describe("the work of a download", () => {
  it("is of its series and episode, names the voice-over, tells the stages, the attempts and the figures", () => {
    const view = downloadWork(download())
    expect([view.kind, view.seriesId, view.episode, view.voiceNumber, view.what, view.state, view.ref]).toEqual(
      ["download", 88, "s06e08", 15, "Kazoku Project", "download", "88:s06e08:dub:Kazoku Project"])
    expect(view.steps.map((step) => step.state)).toEqual(["done", "done", "active", "skipped", "pending"])
    expect(view.steps[0].label).toBe(stateLabel("queued"))
    expect(view.bar).toEqual({ percent: 47 })
    expect(view.facts.find((each) => each.key === t("download.label.variant"))?.mark).toEqual({ n: 1, color: 0, place: "old.yummyani.me" })
    expect(view.facts.map((each) => each.key)).toContain(t("download.label.loaded"))
    expect(view.log.map((line) => line.title ?? line.outcome)).toEqual([t("download.failed.link"), t("download.replan"), stateWord("download")])
    expect(view.log[0].ok).toBe(false)
    expect(view.action).toBeUndefined()
  })

  it("a failed one says that no variant is left and can be removed; a finished one says what it got", () => {
    const failed = downloadWork(download({ status: "failed", finished_at: "2026-11-10T09:21:15+00:00", log: [
      { kind: "attempt", variant: cvh, attempt: 3, of: 3, outcome: "failed", stage: "link", seconds: 126 },
      { kind: "exhausted", variant: null, attempt: 0, of: 0, outcome: "running", stage: null, seconds: 0 }] }))
    expect(failed.state).toBe("failed")
    expect(failed.action).toEqual({ name: "remove", label: t("download.remove"), danger: true })
    expect(failed.log.at(-1)?.title).toBe(t("download.exhausted"))
    expect(failed.facts.find((each) => each.key === t("download.label.outcome"))?.value).toBe(t("download.outcome.dub"))
    const done = downloadWork(download({ status: "done", finished_at: "2026-11-10T09:22:25+00:00",
      stages: stages(["done", "done", "done", "skipped", "done"]), figures: { got_height: 720, got_seconds: 1452, size_mb: 664 } }))
    expect(done.state).toBe("done")
    expect(done.facts.find((each) => each.key === t("download.label.got"))?.value).toContain("Kodik")
    expect(done.facts.find((each) => each.key === t("download.label.quality"))?.value).toBe("720p")
    expect(done.bar).toBeUndefined()
  })

  it("a download that waits tells its place in the queue", () => {
    const view = downloadWork(download({ status: "queued", position: 3, running: 4, limit: 4, figures: {}, log: [] }))
    expect(view.state).toBe("queued")
    expect(view.facts.find((each) => each.key === t("download.label.position"))?.value).toBe("3")
    expect(view.facts.find((each) => each.key === t("download.label.running"))?.value).toBe("4 из 4")
  })
})

const stagesOfPack = (states: string[]) => ["queue", "build", "verify", "apply"].map((name, index) =>
  ({ name, state: states[index], seconds: 12 })) as PackCard["stages"]
const pack = (over: Partial<PackCard> = {}): PackCard => ({
  id: "1:s01e704", series_id: 1, series_name: "Боевой мастер", sync_started: "2026-11-10T09:17:00+00:00", episode: "s01e704",
  name: "e704.mkv", video: { source_id: 7, source_n: 1, source_color: 0, place: "1.ruchime.org", player: "vk", player_label: "VK", height: 1080 },
  status: "build", stages: stagesOfPack(["done", "active", "pending", "pending"]),
  tracks: [{ number: 1, label: "ПВА ШОУ", kind: "own" }, { number: 2, label: "ruchime", kind: "kept" },
           { number: 3, label: "AniStar", kind: "new" }, { number: 4, label: "Old", kind: "gone" }],
  reason: "", waited: false, figures: { progress: 54, written_mb: 812, speed_x: 103, eta: 12 },
  queued_at: "2026-11-10T09:18:00+00:00", finished_at: "", seconds: 61, ...over,
} as PackCard)

describe("the work of a packing", () => {
  it("shows the tracks of the container by where each comes from, what changed, and the progress of the build", () => {
    const view = packWork(pack())
    expect([view.kind, view.seriesId, view.episode, view.state]).toEqual(["pack", 1, "s01e704", "build"])
    expect(view.cells.map((cell) => [cell.n, cell.kind])).toEqual([[1, "own"], [2, "kept"], [3, "new"], [4, "gone"]])
    expect(view.facts.filter((each) => /^[+−]/.test(each.key))).toEqual([
      { key: "+ 3", value: "AniStar" }, { key: "− 4", value: "Old", bad: true }])
    expect(view.bar).toEqual({ percent: 54 })
    expect(view.action).toBeUndefined()
  })

  it("a container built waits for the journal in no stage; a failed one says why and can be removed", () => {
    const built = packWork(pack({ status: "built", stages: stagesOfPack(["done", "done", "done", "pending"]), figures: { wait_s: 145 } }))
    expect(built.state).toBe(PACK_STATES.built)
    expect(built.steps.some((step) => step.state === "active")).toBe(false)
    expect(built.facts.find((each) => each.key === t("pack.label.waits"))?.value).toBe(t("pack.value.waits", { time: "2:25" }))
    expect(built.bar).toBeUndefined()
    const failed = packWork(pack({ status: "failed", waited: true }))
    expect(failed.facts.find((each) => each.key === t("download.label.outcome"))?.value).toBe(t("pack.outcome.waited"))
    expect(failed.action?.name).toBe("remove")
  })

  it("the journal of a series goes step by step, with the file under way", () => {
    const view = journalWork({ series_id: 40, series_name: "Владыка", sync_started: "2026-11-11T02:44:35+00:00", kind: "sync",
      steps_of: 507, steps_done: 230, step: "retag", file: "Ten Thousand Worlds s01e394.mkv.off", started_at: "2026-11-11T02:49:13+00:00", seconds: 341 })
    expect([view.kind, view.seriesId, view.state, view.episode]).toEqual(["journal", 40, "apply", t("pack.journal.kind.sync")])
    expect(view.bar).toEqual({ percent: 45 })
    expect(view.facts).toEqual([{ key: t("pack.journal.stepsKey"), value: "230 из 507" },
      { key: t("pack.journal.step.retag"), value: "Ten Thousand Worlds s01e394.mkv.off" }])
  })
})

const result = (extra: Partial<ConformResult> = {}): ConformResult => ({
  dub: "a.mp4", plot: null, ok: true, refused: false, skipped: false, resid_ms: 5.7, coverage: 1, cuts: 0, suspect: false,
  warnings: [], critical: [], error: null, ...extra,
})
const job = (extra: Partial<ConformTask> = {}): ConformTask => ({
  id: "7f3a91c2-0000", label: "Show s01e03", key: "plan:46:aa", series_id: 46, status: "running", progress: 0.4, stage: "band", stage_pct: 0.1,
  detail: "", dub_index: 2, dub_total: 3, cur_dub: "b.mp4", dubs: ["a.mp4", "b.mp4", "c.mp4"], elapsed_s: 72,
  created_at: "2026-10-30T02:14:00+00:00", error: null,
  results: [result({ plot: "a__track.png" })], ...extra,
})
const dubName = (file: string) => file.replace(/\.mp4$/, "").toUpperCase()

describe("the work of an alignment", () => {
  it("is of the series its key says, and lists its voice-overs: the one with a verdict and a plot, the one under way, the one that waits", () => {
    const view = conformWork(job(), dubName)
    expect([view.kind, view.seriesId, view.episode, view.what, view.state]).toEqual(["conform", 46, "s01e03", "3 озвучки", "align"])
    expect(view.bar).toEqual({ percent: 40, label: "band" })
    expect(view.rows.map((row) => [row.name, row.word, row.state])).toEqual([
      ["A", capital(t("agents.conform.verdict.accepted")), "done"],
      ["B", `${stateLabel("align")} ${t("agents.conform.dub.position", { index: 2, total: 3 })}`, "align"],
      ["C", stateLabel("queued"), "queued"]])
    expect(view.rows[0].plot?.src).toBe("/api/conform/jobs/7f3a91c2-0000/plot/a__track.png")
    expect(view.foot.at(-1)).toEqual({ key: t("work.job"), value: "7f3a91c2" })
  })

  it("draws a verdict by its own word: put but not vouched for is yellow, turned down and failed are red", () => {
    const view = conformWork(job({ status: "done", results: [
      result({ dub: "a.mp4", critical: ["drift"] }), result({ dub: "b.mp4", ok: false, refused: true }), result({ dub: "c.mp4", ok: false })] }), dubName)
    expect(view.rows.map((row) => [row.word, row.state])).toEqual([
      [capital(t("agents.conform.verdict.unsure")), "absent"], [capital(t("agents.conform.verdict.rejected")), "failed"],
      [capital(t("agents.conform.verdict.failed")), "failed"]])
  })

  it("is stopped while it runs, cancelled while it waits and deleted once it ended; a job of another maker has no series", () => {
    const action = (status: string) => conformWork(job({ status }), dubName).action
    expect(action("running")).toEqual({ name: "stop", label: t("agents.conform.action.stop"), danger: false })
    expect(action("queued")?.name).toBe("cancel")
    expect(action("paused")?.name).toBe("cancel")
    expect(action("done")).toEqual({ name: "delete", label: t("agents.conform.action.delete"), danger: true })
    expect(conformWork(job({ series_id: null }), dubName).seriesId).toBeNull()
  })
})

const slicing = (over: Partial<SlicingCard> = {}): SlicingCard => ({
  id: 3, series_id: 40, unique_id: "comp1", status: "slicing", file: "Lord.S01.mkv", season: 1, episode_start: 11, episode_end: 13,
  episodes: [{ number: 11, state: "done" }, { number: 12, state: "active" }, { number: 13, state: "pending" }],
  error: null, created_at: "2026-11-13 08:38:12.000000", ...over,
})

describe("the work of a slicing", () => {
  it("writes the compilation by its episodes and shows each cut, with the share cut", () => {
    expect(slotLabel(1, 11, 13)).toBe("s01e11–e13")
    expect(slotLabel(2, 5, 5)).toBe("s02e05")
    const view = slicingWork(slicing())
    expect([view.kind, view.seriesId, view.episode, view.what, view.state]).toEqual(["slicing", 40, "s01e11–e13", t("work.kind.compilation"), "slice"])
    expect(view.cells.map((cell) => [cell.n, cell.kind])).toEqual([[11, "done"], [12, "active"], [13, "pending"]])
    expect(view.bar).toEqual({ percent: 33 })
    expect(view.facts[0].value).toBe("1 из 3")
  })

  it("a task that stopped says on which episode, and a task that waits has no progress", () => {
    const failed = slicingWork(slicing({ status: "error", episodes: [{ number: 11, state: "done" }, { number: 12, state: "failed" }, { number: 13, state: "pending" }],
      error: '{"code":"slicing_ffmpeg_failed","episode":12,"detail":"битый поток"}' }))
    expect(failed.state).toBe("failed")
    expect(failed.note).toContain("12")
    expect(failed.cells[1].kind).toBe("failed")
    expect(slicingWork(slicing({ status: "pending" })).bar).toBeUndefined()
  })
})

describe("the work of a torrent", () => {
  it("the processing stands at one of four stages and is of its series", () => {
    const task: QueueTask = { hash: "3f9a1c4e0b72", series_id: 39, torrent_id: "14022", stage: "renaming" }
    const view = processingWork(task)
    expect([view.kind, view.seriesId, view.state, view.what]).toEqual(["processing", 39, "renaming", t("work.torrent", { id: "14022" })])
    expect(view.steps.map((step) => step.state)).toEqual(["done", "active", "pending", "pending"])
    expect(processingWork({ ...task, stage: "error" }).steps.every((step) => step.state === "pending")).toBe(true)
    expect(processingWork({ ...task, stage: "error" }).state).toBe("failed")
  })

  it("the monitor tells the progress, the speed in megabytes and what is left; a failed torrent says why", () => {
    const task: QueueTask = { qb_hash: "3f9a1c4e0b72", series_id: 39, series_name: "Путешествие", status: "downloading", progress: 48,
      dlspeed: 13002342, eta: 1268 }
    const view = monitorWork(task)
    expect([view.kind, view.seriesId, view.state, view.bar]).toEqual(["monitor", 39, "download", { percent: 48 }])
    expect(view.facts).toEqual([{ key: t("download.label.speed"), value: t("download.value.mbs", { value: "12,4" }) },
      { key: t("download.label.left"), value: "21:08" }])
    const stalled = monitorWork({ ...task, status: "stalledDL", dlspeed: 0 })
    expect(stalled.state).toBe("idle")
    expect(stalled.facts).toEqual([])
    const broken = monitorWork({ ...task, status: "error", error_message: '{"code":"qbt_error"}' })
    expect([broken.state, broken.note]).toEqual(["failed", t("reason.qbt_error")])
    expect(monitorWork({ ...task, status: "somethingNew" }).word).toBe("somethingNew")
  })
})

describe("the work of a read of a page", () => {
  it("is of the series that owns the page, tells the phase and how far, and is stopped by its address", () => {
    const view = readWork({ seriesId: 54, sourceId: 8, url: "https://vkvideo.ru/@pvashow|Противостоящий небесам", phase: "videos",
      current: 12, total: 40, rate: 3.1 })
    expect([view.kind, view.seriesId, view.state, view.ref]).toEqual(["read", 54, "read", "https://vkvideo.ru/@pvashow|Противостоящий небесам"])
    expect(view.action).toEqual({ name: "stopRead", label: t("sources.stopRead"), danger: false })
    expect(view.bar).toEqual({ percent: 30 })
    expect(view.facts).toEqual([{ key: t("sources.phase.videos"), value: "12 из 40" }, { key: t("download.label.speed"), value: "3.1 с/с" }])
  })
})
