// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { defineComponent, h } from "vue"
import { flushPromises, mount, type VueWrapper } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { useSeriesOutput } from "./useSeriesOutput"
import * as calls from "./outputCalls"
import type { Composition } from "./outputCalls"
import type { Answer } from "./sourceCalls"
import { useSSE } from "./useSSE"
import { useCompositionStore } from "../stores/composition"
import { usePlanStore } from "../stores/plan"
import { groupTarget, resetBody, setBody, type Reference, type Row } from "../lib/output"
import { SPECS, AVAILABILITY } from "../lib/output.fixtures"
import type { PlannerView } from "../lib/plan"
import { asset, assetTrack, goal, goalTrack, goalVideo, planView, rowRef } from "../lib/plan.fixtures"
import type { SourceSummary } from "../lib/sources"

vi.mock("./outputCalls")

const PAGE = "https://1.ruchime.org/release/test"
const ok = <T>(data: T): Answer<T> => ({ ok: true, data })

const row = (over: Partial<Row> = {}): Row => ({
  series_id: 1, source_id: 7, row_key: "row", excluded: false, season: 1, episode_start: 2, episode_end: 2,
  voice_label: "Ruchime", voice_name: "Ruchime", player: "vibix", player_label: "", group: "", text: "",
  heights: [1080], state: "playable", link: "", video: "", ...over,
})
const reference = (over: Partial<Reference> = {}): Reference => ({
  season: 1, episode: 2, part: null, mark: "by_level", source_id: 7, row_key: "row", player: "vibix",
  voice_group: "Ruchime", height: 1080, reason: null, compilation: null, ...over,
})
const summary: SourceSummary = {
  source_id: 7, url: PAGE, status: "no_profile",
  players: [{ player: "vk", player_label: "VK", rows: 3, groups: 0 }],
}
const composition = (over: Partial<Composition> = {}): Composition => ({
  series_id: 1, rev: 1, rows: [row()], sources: [summary],
  levels: [{ season: 1, levers: [{ lever: "voice.take", target: groupTarget("Ruchime"), value: false, level: "season" }] }],
  settings: [{ season: 1, episode: 2, levers: [{ lever: "voice.take", target: groupTarget("Ruchime"), value: true, level: "episode" }] }],
  levers: SPECS,
  availability: AVAILABILITY,
  ...over,
})
// Episode 2 is a file the plan holds, and its voice-over AniStar is being realigned while the sync runs.
const planned = planView({
  running: true,
  assets: [asset("S01E02.mp4", rowRef(7, "row"))],
  episodes: [goal([1, 2], goalVideo("asset", rowRef(7, "row"), "S01E02.mp4"),
    [goalTrack("AniStar", "realign", rowRef(8, "other"), { player: "kodik" })])],
})

// The plan reaches the window by its read and by the store; a test gives both the same view, references included.
const givePlan = (view: PlannerView, references: Reference[] = []): void => {
  const whole = { ...view, references }
  vi.mocked(calls.fetchPlan).mockResolvedValue(ok(whole))
  usePlanStore().apply(whole)
}

let mounted: VueWrapper[] = []

function setup(kind: "torrent" | "video" = "video") {
  let output!: ReturnType<typeof useSeriesOutput>
  mounted.push(mount(defineComponent({ setup() { output = useSeriesOutput(1, kind); return () => h("div") } })))
  return output
}

beforeEach(() => {
  setActivePinia(createPinia())
  useSSE().connected.value = false
  vi.mocked(calls.fetchComposition).mockReset().mockResolvedValue(ok(composition()))
  vi.mocked(calls.fetchPlan).mockReset().mockResolvedValue(ok(planView()))
  vi.mocked(calls.deleteFromPlan).mockReset().mockResolvedValue(ok({ success: true }))
  vi.mocked(calls.setLever).mockReset().mockResolvedValue(ok({ success: true }))
  vi.mocked(calls.resetLever).mockReset().mockResolvedValue(ok({ success: true }))
  vi.mocked(calls.syncSeries).mockReset().mockResolvedValue(ok({ success: true }))
  vi.mocked(calls.resetFailures).mockReset().mockResolvedValue(ok({ success: true, reset: 1 }))
  vi.mocked(calls.switchOutput).mockReset().mockResolvedValue(ok({ success: true }))
  vi.mocked(calls.fetchRenames).mockReset().mockResolvedValue(ok({
    preview: [{ unique_id: "u1", season: 1, episode: 2, part: null, new_filename_preview: "Show s01e02.mp4" }] }))
})

afterEach(() => mounted.splice(0).forEach((wrapper) => wrapper.unmount()))

describe("useSeriesOutput", () => {
  it("reads the layout and the plan of a video series and joins the references of the plan into episodes", async () => {
    givePlan(planned, [reference()])
    const output = setup()
    await flushPromises()
    expect(calls.fetchComposition).toHaveBeenCalledWith(1)
    expect(calls.fetchPlan).toHaveBeenCalledWith(1)
    expect(output.views.value).toEqual([{
      season: 1, episode: 2, references: [reference()], rows: [row()],
      levers: [{ lever: "voice.take", target: groupTarget("Ruchime"), value: true, level: "episode" }],
      dubs: [expect.objectContaining({ voice_label: "AniStar", state: "running", source_id: 8, player: "kodik" })],
      files: [{ unique_id: "1:2", source_id: 7, row_key: "row", status: "completed", cut: null, pending_cut: null }],
      labels: { "7:vk": "VK" },
      availability: composition().availability,
    }])
    expect(output.levels.value).toEqual(composition().levels)
    expect(output.sources.value).toEqual([summary])
    expect(output.seasons.value.map((group) => group.season)).toEqual([1])
    expect(output.failure.value).toBeNull()
    expect(output.loading.value).toBe(false)
  })

  it("reads nothing for a torrent series and does not follow its layout or the connection", async () => {
    const output = setup("torrent")
    await flushPromises()
    useSSE().connected.value = true
    useCompositionStore().applyChanged({ series_id: 1, rev: 1, episodes: [], summary_only: false })
    await flushPromises()
    expect(calls.fetchComposition).not.toHaveBeenCalled()
    expect(calls.fetchPlan).not.toHaveBeenCalled()
    expect(output.composition.value).toBeNull()
    expect(output.references.value).toEqual([])
    expect(output.views.value).toEqual([])
  })

  it("takes the references of the window from a packet of the plan, without a new read", async () => {
    givePlan(planned, [reference()])
    const output = setup()
    await flushPromises()
    const plans = vi.mocked(calls.fetchPlan).mock.calls.length
    usePlanStore().apply({ ...planned, references: [
      reference({ mark: "own", source_id: 8, row_key: "other", player: "kodik", voice_group: "AniStar", height: 720 }),
      reference({ episode: 3 }),
    ] })
    await flushPromises()
    expect(output.references.value.map((each) => [each.episode, each.mark])).toEqual([[2, "own"], [3, "by_level"]])
    expect(output.views.value[0].references.map((each) => each.mark)).toEqual(["own"])
    expect(calls.fetchPlan).toHaveBeenCalledTimes(plans)
    expect(calls.fetchComposition).toHaveBeenCalledTimes(1)
  })

  it("reads the plan again when the layout gets a newer revision, and not for the same one or for another series", async () => {
    setup()
    await flushPromises()
    const before = vi.mocked(calls.fetchComposition).mock.calls.length
    const plansBefore = vi.mocked(calls.fetchPlan).mock.calls.length
    const store = useCompositionStore()
    store.applyChanged({ series_id: 1, rev: 1, episodes: [], summary_only: false })
    await flushPromises()
    expect(calls.fetchComposition).toHaveBeenCalledTimes(before + 1)
    store.applyChanged({ series_id: 1, rev: 1, episodes: [], summary_only: false })
    await flushPromises()
    expect(calls.fetchComposition).toHaveBeenCalledTimes(before + 1)
    store.applyChanged({ series_id: 2, rev: 5, episodes: [], summary_only: false })
    await flushPromises()
    expect(calls.fetchComposition).toHaveBeenCalledTimes(before + 1)
    store.applyChanged({ series_id: 1, rev: 2, episodes: [], summary_only: false })
    await flushPromises()
    expect(calls.fetchComposition).toHaveBeenCalledTimes(before + 2)
    expect(calls.fetchPlan).toHaveBeenCalledTimes(plansBefore + 2)
  })

  it("keeps the newest reply when an older read arrives late", async () => {
    let late!: (answer: Answer<Composition>) => void
    vi.mocked(calls.fetchComposition)
      .mockImplementationOnce(() => new Promise<Answer<Composition>>((resolve) => { late = resolve }))
      .mockResolvedValueOnce(ok(composition({ rev: 2 })))
    const output = setup()
    useCompositionStore().applyChanged({ series_id: 1, rev: 2, episodes: [], summary_only: false })
    await flushPromises()
    expect(output.composition.value?.rev).toBe(2)
    late(ok(composition({ rev: 1 })))
    await flushPromises()
    expect(output.composition.value?.rev).toBe(2)
  })

  it("shows the words of a refused layout and keeps the references of the plan that did come", async () => {
    givePlan(planned, [reference()])
    vi.mocked(calls.fetchComposition).mockResolvedValue({ ok: false, text: "Выход не построен" })
    const output = setup()
    await flushPromises()
    expect(output.failure.value).toBe("Выход не построен")
    expect(output.composition.value).toBeNull()
    expect(output.references.value).toEqual([reference()])
  })

  it("sends a change with the bodies that setBody and resetBody build, and returns a refusal in words", async () => {
    const output = setup()
    await flushPromises()
    const target = groupTarget("Ruchime")
    expect(await output.set({ season: 1, episode: 2 }, "voice.take", target, false)).toBeNull()
    expect(calls.setLever).toHaveBeenCalledWith(1, setBody({ season: 1, episode: 2 }, "voice.take", target, false))
    expect(vi.mocked(calls.setLever).mock.calls[0][1]).toStrictEqual({
      season: 1, episode: 2, lever: "voice.take", target: "group:Ruchime", value: false,
    })

    expect(await output.reset({ season: 1 }, "voice.take")).toBeNull()
    expect(vi.mocked(calls.resetLever).mock.calls[0][1]).toStrictEqual(resetBody({ season: 1 }, "voice.take"))
    expect(vi.mocked(calls.resetLever).mock.calls[0][1]).toStrictEqual({ season: 1, lever: "voice.take" })

    vi.mocked(calls.resetLever).mockResolvedValueOnce({ ok: false, text: "Уровень не найден" })
    expect(await output.reset({ season: 9 })).toBe("Уровень не найден")
    expect(output.failure.value).toBe("Уровень не найден")
    expect(calls.fetchComposition).toHaveBeenCalledTimes(1)
  })

  it("holds busy while a lever is on its way and syncing while a pass runs, and words a refused pass", async () => {
    let finish!: (value: Answer<unknown>) => void
    vi.mocked(calls.setLever).mockImplementationOnce(() => new Promise((resolve) => { finish = resolve }))
    const output = setup()
    await flushPromises()
    const pending = output.set({ season: 1 }, "voice.take", groupTarget("Ruchime"), false)
    expect(output.busy.value).toBe(true)
    expect(output.syncing.value).toBe(false)
    finish(ok({ success: true }))
    await pending
    expect(output.busy.value).toBe(false)

    vi.mocked(calls.syncSeries).mockResolvedValueOnce({ ok: false, text: "Проход уже идёт" })
    const failed = output.sync()
    expect(output.syncing.value).toBe(true)
    expect(await failed).toBe("Проход уже идёт")
    expect(output.syncing.value).toBe(false)
    expect(output.failure.value).toBe("Проход уже идёт")
    expect(calls.syncSeries).toHaveBeenCalledWith(1)

    const syncs = vi.mocked(calls.syncSeries).mock.calls.length
    expect(await output.retryFailed()).toBeNull()
    expect(calls.resetFailures).toHaveBeenCalledWith(1)
    expect(vi.mocked(calls.syncSeries).mock.calls.length).toBe(syncs) // the reset is no sync
  })

  it("switches an episode or a season off and on with the body the core reads, and returns a refusal in words", async () => {
    const output = setup()
    await flushPromises()
    expect(await output.switchTo(1, 2, true)).toBeNull()
    expect(await output.switchTo(1, null, false)).toBeNull()
    expect(vi.mocked(calls.switchOutput).mock.calls).toEqual([[1, { season: 1, episode: 2, off: true }], [1, { season: 1, off: false }]])
    vi.mocked(calls.switchOutput).mockResolvedValueOnce({ ok: false, text: "Сериал не найден" })
    expect(await output.switchTo(9, null, true)).toBe("Сериал не найден")
    expect(output.failure.value).toBe("Сериал не найден")
  })

  it("reads the names of the files with the layout, and again with every new layout", async () => {
    const output = setup()
    await flushPromises()
    expect(output.names.value.map((row) => row.new_filename_preview)).toEqual(["Show s01e02.mp4"])
    useCompositionStore().applyChanged({ series_id: 1, rev: 9, episodes: [], summary_only: false })
    await flushPromises()
    expect(calls.fetchRenames).toHaveBeenCalledTimes(2)
  })

  it("shows the words of a refusal of the names and keeps the ones it had", async () => {
    const output = setup()
    await flushPromises()
    vi.mocked(calls.fetchRenames).mockResolvedValueOnce({ ok: false, text: "Нет связи с сервером" })
    useCompositionStore().applyChanged({ series_id: 1, rev: 9, episodes: [], summary_only: false })
    await flushPromises()
    expect(output.failure.value).toBe("Нет связи с сервером")
    expect(output.names.value).toHaveLength(1)
  })

  it("reads the plan again when the connection comes back", async () => {
    setup()
    await flushPromises()
    const before = vi.mocked(calls.fetchComposition).mock.calls.length
    const plansBefore = vi.mocked(calls.fetchPlan).mock.calls.length
    useSSE().connected.value = true
    await flushPromises()
    expect(calls.fetchComposition).toHaveBeenCalledTimes(before + 1)
    expect(calls.fetchPlan).toHaveBeenCalledTimes(plansBefore + 1)
  })

  it("does the action of a track: a failure is reset, a track goes out of its file, the video goes for another", async () => {
    givePlan(planView({
      assets: [asset("S01E02.mp4", rowRef(7, "row"), [assetTrack("AniStar", rowRef(8, "other"))])],
      episodes: [goal([1, 2], goalVideo("asset", rowRef(7, "row"), "S01E02.mp4"), [goalTrack("AniStar", "reuse", rowRef(8, "other"))])],
    }))
    const output = setup()
    await flushPromises()
    expect(await output.retry(1, 2, "AniStar")).toBeNull()
    expect(calls.resetFailures).toHaveBeenCalledWith(1)
    expect(await output.redo(1, 2, "AniStar")).toBeNull()
    expect(calls.deleteFromPlan).toHaveBeenLastCalledWith(1, { kind: "track", name: "S01E02.mp4", dub: rowRef(8, "other") })
    expect(await output.refetch(1, 2, null)).toBeNull()
    expect(calls.deleteFromPlan).toHaveBeenLastCalledWith(1, { kind: "video", name: "S01E02.mp4" })
    // A track the file does not hold is named by no command.
    expect(await output.redo(1, 2, "Nobody")).toBeNull()
    expect(calls.deleteFromPlan).toHaveBeenCalledTimes(2)
  })
})
