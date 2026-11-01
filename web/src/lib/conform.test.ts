import { describe, expect, it } from "vitest"
import type { ConformResult, ConformTask } from "../stores/queues"
import { dubRows, footText, formatDuration, isAccepted, isActive, resultMetrics, splitTitle } from "./conform"

const result = (extra: Partial<ConformResult> = {}): ConformResult => ({
  dub: "a.mp4", plot: null, ok: true, skipped: false, resid_ms: 5.7, coverage: 1, cuts: 0, suspect: false,
  warnings: [], critical: [], error: null, ...extra,
})

const task = (extra: Partial<ConformTask> = {}): ConformTask => ({
  id: "cf1", label: "Show s01e03", key: null, status: "running", progress: 0.4, stage: "band", stage_pct: 0.1,
  detail: "", dub_index: 1, dub_total: 2, cur_dub: "a.mp4", dubs: ["a.mp4", "b.mp4"], elapsed_s: 192,
  created_at: "2026-10-30T02:14:00+00:00", error: null, results: [], ...extra,
})

describe("splitTitle", () => {
  it("takes the episode tag off the label the service holds", () => {
    expect(splitTitle("Стать победителем s01e03")).toEqual({ title: "Стать победителем", episode: "S01E03" })
  })

  it("keeps a label without a tag whole", () => {
    expect(splitTitle("Something else")).toEqual({ title: "Something else", episode: "" })
  })
})

describe("isActive", () => {
  it("is true for a job that waits or runs and false for one that ended", () => {
    expect(["queued", "paused", "running"].every(isActive)).toBe(true)
    expect(["done", "failed", "cancelled"].some(isActive)).toBe(false)
  })
})

describe("isAccepted", () => {
  it("follows the verdict of the service alone: what it adds, suspect or critical, is what it found and put right", () => {
    expect(isAccepted(result())).toBe(true)
    expect(isAccepted(result({ suspect: true }))).toBe(true)
    expect(isAccepted(result({ critical: ["no common sound"] }))).toBe(true)
    expect(isAccepted(result({ ok: false }))).toBe(false)
  })
})

describe("resultMetrics", () => {
  it("shows the rest and the coverage, with a decimal comma", () => {
    expect(resultMetrics(result())).toBe("остаток 5,7 мс, покрытие 100%")
    expect(resultMetrics(result({ skipped: true }))).toBe("уже выровнена")
  })
})

describe("formatDuration", () => {
  it("names minutes and seconds, seconds of a long run in two digits", () => {
    expect(formatDuration(192)).toBe("3 мин 12 с")
    expect(formatDuration(182)).toBe("3 мин 02 с")
    expect(formatDuration(8)).toBe("8 с")
  })
})

describe("footText", () => {
  it("tells when a waiting job was added and how long a running or an ended one took", () => {
    expect(footText(task({ status: "queued" })).startsWith("добавлена в ")).toBe(true)
    expect(footText(task())).toBe("время выполнения: 3 мин 12 с")
    expect(footText(task({ status: "done", elapsed_s: 8 }))).toBe("время выполнения: 8 с")
  })
})

describe("dubRows", () => {
  const name = (file: string) => file.replace(/\.mp4$/, "").toUpperCase()

  it("shows the voice-over in work, the one that waits and the one that has a result", () => {
    const rows = dubRows(task(), name)
    expect(rows.map((row) => [row.name, row.state])).toEqual([["A", "running"], ["B", "waiting"]])
    const later = dubRows(task({ results: [result()], cur_dub: "b.mp4" }), name)
    expect(later.map((row) => row.state)).toEqual(["result", "running"])
  })

  it("marks the voice-overs of a cancelled job and hides those a failed job never ran", () => {
    expect(dubRows(task({ status: "cancelled" }), name).map((row) => row.state)).toEqual(["cancelled", "cancelled"])
    expect(dubRows(task({ status: "failed", results: [result()] }), name).map((row) => row.state)).toEqual(["result"])
    expect(dubRows(task({ status: "failed" }), name)).toEqual([])
  })

  it("falls back to the results when the job names no files", () => {
    const rows = dubRows(task({ dubs: [], status: "done", results: [result(), result({ dub: "b.mp4" })] }), name)
    expect(rows.map((row) => row.file)).toEqual(["a.mp4", "b.mp4"])
  })
})
