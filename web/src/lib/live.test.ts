import { describe, expect, it } from "vitest"
import type { ConformResult, ConformTask, DownloadCard, PackCard } from "../stores/queues"
import { dubKey, liveEntries, liveFrom, slotKey } from "./live"

const download = (status: string, kind: "video" | "dub", episode: string, group: string | null = null, series = 5): DownloadCard =>
  ({ id: `${kind}-${episode}-${group}`, status, need: { series_id: series, episode, kind, group } }) as unknown as DownloadCard

const pack = (status: string, episode: string, series = 5): PackCard => ({ id: `pack-${episode}`, series_id: series, episode, status }) as unknown as PackCard

const result = (dub: string, extra: Partial<ConformResult> = {}): ConformResult => ({
  dub, plot: null, ok: true, refused: false, skipped: false, resid_ms: 5, coverage: 1, cuts: 0, suspect: false, warnings: [],
  critical: [], error: null, ...extra,
})

const job = (extra: Partial<ConformTask> = {}): ConformTask => ({
  id: "plan:5:abc", label: "Show s01e03", key: "plan:5:abc", series_id: 5, status: "running", progress: 0.4, stage: "band",
  stage_pct: 0.1, detail: "", dub_index: 1, dub_total: 3, cur_dub: "A.mka", dubs: ["A.mka", "B.mka", "C.mka"], elapsed_s: 10,
  created_at: null, error: null, results: [], ...extra,
})

const nameOf = (_job: string, file: string): string => file.replace(/\.[^.]+$/, "")

describe("liveEntries", () => {
  it("tells each piece of work of the series by its episode and voice-over, and leaves the cards of other series alone", () => {
    const entries = liveEntries(5, [
      download("download", "video", "S01E01"), download("queued", "video", "S01E02"), download("failed", "video", "S01E03"),
      download("remux", "dub", "S01E01", "AniStar"), download("queued", "dub", "S01E01", "Kodik"), download("done", "dub", "S01E01", "Old"),
      download("download", "video", "S01E01", null, 6),
    ], [], [], nameOf)
    const live = liveFrom(entries)
    expect([...live.videos]).toEqual([["1:1", "downloading"], ["1:2", "queued"], ["1:3", "error"]])
    expect(live.dubs.get(dubKey(1, 1, "AniStar"))).toBe("downloading")
    expect(live.dubs.get(dubKey(1, 1, "Kodik"))).toBe("download_queued")
    expect(live.dubs.has(dubKey(1, 1, "Old"))).toBe(false)
  })

  it("tells an alignment per voice-over: the one the service runs, those waiting, and the one it turned down", () => {
    const task = job({ results: [result("B.mka", { ok: false, refused: true })] })
    const live = liveFrom(liveEntries(5, [], [task, job({ id: "other", series_id: 6 })], [], nameOf))
    expect(live.dubs.get(dubKey(1, 3, "A"))).toBe("running")
    expect(live.dubs.get(dubKey(1, 3, "B"))).toBe("error")
    expect(live.dubs.get(dubKey(1, 3, "C"))).toBe("align_queued")
    expect(live.dubs.size).toBe(3)
  })

  it("does not colour a voice-over the service accepted, nor the job of a label that names no one episode", () => {
    const done = job({ status: "done", cur_dub: "", results: [result("A.mka"), result("B.mka", { critical: ["x"] }), result("C.mka")] })
    expect(liveEntries(5, [], [done], [], nameOf)).toEqual([])
    expect(liveEntries(5, [], [job({ label: "Show s01e09-e12" })], [], nameOf)).toEqual([])
  })

  it("names a voice-over the way the planner names its file, so a voice with a slash or a colon is found", () => {
    const entries = liveEntries(5, [download("download", "dub", "S01E01", "AniLibria: TV/HD")], [], [], nameOf)
    expect(entries).toEqual([["dub", "1:1:AniLibria TVHD", "downloading"]])
    expect(dubKey(1, 1, "AniLibria: TV/HD")).toBe("1:1:AniLibria TVHD")
  })

  it("tells the container of an episode: queued, being built or put in place, or failed; one that is done is no work", () => {
    const live = liveFrom(liveEntries(5, [], [], [
      pack("queued", "S01E01"), pack("build", "S01E02"), pack("apply", "S01E03"), pack("failed", "S01E04"), pack("done", "S01E05"),
      pack("build", "S01E06", 6),
    ], nameOf))
    expect([...live.packs]).toEqual([["1:1", "queued"], ["1:2", "pack"], ["1:3", "pack"], ["1:4", "failed"]])
  })

  it("equals itself while only the figures of the work change, so that the window recounts on a state alone", () => {
    const one = liveEntries(5, [{ ...download("download", "video", "S01E01"), figures: { share: 0.1 } } as unknown as DownloadCard], [], [], nameOf)
    const two = liveEntries(5, [{ ...download("download", "video", "S01E01"), figures: { share: 0.7 } } as unknown as DownloadCard], [], [], nameOf)
    expect(JSON.stringify(one)).toBe(JSON.stringify(two))
    expect(slotKey(1, 1)).toBe("1:1")
  })
})
