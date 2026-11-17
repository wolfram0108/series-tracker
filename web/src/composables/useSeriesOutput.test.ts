// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { defineComponent, h } from "vue"
import { flushPromises, mount, type VueWrapper } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { useSeriesOutput } from "./useSeriesOutput"
import * as calls from "./outputCalls"
import type { Composition, Queues } from "./outputCalls"
import type { Answer } from "./sourceCalls"
import { useSSE } from "./useSSE"
import { useCompositionStore } from "../stores/composition"
import { usePlanStore } from "../stores/plan"
import { groupTarget, resetBody, setBody, variantTarget, type QueueRow, type Reference, type Row } from "../lib/output"
import { QUEUE_PAUSE_MS } from "../lib/queueChange"
import { SPECS, AVAILABILITY } from "../lib/output.fixtures"
import type { PlannerView } from "../lib/plan"
import { asset, assetTrack, goal, goalTrack, goalVideo, planView, rowRef } from "../lib/plan.fixtures"
import type { SourceSummary } from "../lib/sources"

vi.mock("./outputCalls")

const PAGE = "https://1.ruchime.org/release/test"
const ok = <T>(data: T): Answer<T> => ({ ok: true, data })

const row = (over: Partial<Row> = {}): Row => ({
  series_id: 1, source_id: 7, in_output: true, row_key: "row", excluded: false, season: 1, episode_start: 2, episode_end: 2,
  voice_label: "Ruchime", voice_name: "Ruchime", player: "vibix", player_label: "", group: "", text: "",
  heights: [1080], state: "playable", link: "", video: "", timeline: "", track: "sound", language: "", subtitle_type: "", stale: false, ...over,
})
const reference = (over: Partial<Reference> = {}): Reference => ({
  season: 1, episode: 2, part: null, mark: "by_level", source_id: 7, row_key: "row", player: "vibix",
  voice_group: "Ruchime", height: 1080, reason: null, compilation: null, picture: null, markup: false, ...over,
})
const summary: SourceSummary = {
  source_id: 7, url: PAGE, status: "no_profile", title: "",
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

function setup() {
  let output!: ReturnType<typeof useSeriesOutput>
  mounted.push(mount(defineComponent({ setup() { output = useSeriesOutput(1); return () => h("div") } })))
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
  vi.mocked(calls.setLevers).mockReset().mockResolvedValue(ok({ success: true }))
  vi.mocked(calls.fetchQueues).mockReset().mockResolvedValue(ok(seasonQueues()))
  vi.mocked(calls.syncSeries).mockReset().mockResolvedValue(ok({ success: true }))
  vi.mocked(calls.resetFailures).mockReset().mockResolvedValue(ok({ success: true, reset: 1 }))
  vi.mocked(calls.switchOutput).mockReset().mockResolvedValue(ok({ success: true }))
})

afterEach(() => {
  mounted.splice(0).forEach((wrapper) => wrapper.unmount())
  vi.useRealTimers()
})

// The queues of the season the boxes tests press: the reference of two variants, the voice-over AniStar of one.
const variant = (group: string, source_id: number, player: string, allowed = true): QueueRow => ({
  group, source_id, player, player_label: player, episodes: "1", stale: "", allowed, allowed_level: allowed ? "default" : "season", language: "", subtitle_type: "", default: false, forced: false,
})
const seasonQueues = (allowed = true): Queues => ({
  series_id: 1, season: 1, episode: null, markup_follows: true,
  reference: { order_level: "default", same_level: "default", rows: [variant("Ruchime", 7, "vibix", allowed), variant("AniStar", 8, "kodik", allowed)] },
  groups: [{ group: "AniStar", take: allowed, take_level: allowed ? "default" : "season", order_level: "default", same_level: "default",
    rows: [variant("AniStar", 8, "kodik", allowed)] }],
})
const timers = (): void => { vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] }) }

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
      dubs: [expect.objectContaining({ voice_label: "AniStar", state: "planned", source_id: 8, player: "kodik" })],
      files: [{ unique_id: "1:2", source_id: 7, row_key: "row", status: "completed", cut: null, pending_cut: null, online: true }],
      labels: { "7:vk": "VK" },
      availability: composition().availability,
    }])
    expect(output.levels.value).toEqual(composition().levels)
    expect(output.sources.value).toEqual([summary])
    expect(output.seasons.value.map((group) => group.season)).toEqual([1])
    expect(output.failure.value).toBeNull()
    expect(output.loading.value).toBe(false)
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

  it("reads the layout again when it gets a newer revision, and not for the same one or for another series — and not the plan, which the core pushes", async () => {
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
    expect(calls.fetchPlan).toHaveBeenCalledTimes(plansBefore) // the plan of the change comes as a packet, counted once
  })

  it("reads the plan for a newer revision when the window has none yet, and its failure shows until a read of it succeeds", async () => {
    vi.mocked(calls.fetchPlan).mockResolvedValueOnce({ ok: false, text: "План не посчитан" })
    const output = setup()
    await flushPromises()
    expect(output.ready.value).toBe(false)
    expect(output.failure.value).toBe("План не посчитан")
    useCompositionStore().applyChanged({ series_id: 1, rev: 1, episodes: [], summary_only: false })
    await flushPromises()
    expect(calls.fetchPlan).toHaveBeenCalledTimes(2)
    expect(output.ready.value).toBe(true)
    expect(output.failure.value).toBeNull()
  })

  it("keeps the failure of the plan while only the layout is read again", async () => {
    vi.mocked(calls.fetchPlan).mockResolvedValue({ ok: false, text: "План не посчитан" })
    const output = setup()
    await flushPromises()
    useCompositionStore().applyChanged({ series_id: 1, rev: 1, episodes: [], summary_only: false })
    await flushPromises()
    expect(output.failure.value).toBe("План не посчитан")
    vi.mocked(calls.fetchComposition).mockResolvedValue({ ok: false, text: "Выход не построен" })
    useCompositionStore().applyChanged({ series_id: 1, rev: 2, episodes: [], summary_only: false })
    await flushPromises()
    expect(output.failure.value).toBe("Выход не построен") // the layout's words first
  })

  it("lets a read of the plan land that a read of the layout overtakes", async () => {
    givePlan(planned, [reference()])
    const output = setup()
    await flushPromises()
    let land!: (answer: Answer<PlannerView>) => void
    vi.mocked(calls.fetchPlan).mockReturnValueOnce(new Promise((resolve) => { land = resolve }))
    useSSE().connected.value = true // the connection came back: both are read
    await flushPromises()
    useCompositionStore().applyChanged({ series_id: 1, rev: 1, episodes: [], summary_only: false }) // a layout read overtakes
    await flushPromises()
    land(ok({ ...planned, references: [reference({ mark: "own", height: 720 })] }))
    await flushPromises()
    expect(output.references.value.map((each) => each.mark)).toEqual(["own"])
    expect(output.loading.value).toBe(false)
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

    expect(await output.sync({ season: 1, episode: 2 })).toBeNull()
    expect(calls.syncSeries).toHaveBeenLastCalledWith(1, { season: 1, episode: 2 })
    const syncs = vi.mocked(calls.syncSeries).mock.calls.length
    expect(await output.retryFailed()).toBeNull()
    expect(calls.resetFailures).toHaveBeenCalledWith(1)
    expect(vi.mocked(calls.syncSeries).mock.calls.length).toBe(syncs) // the reset is no sync
  })

  it("switches episodes or a season off and on with the body the core reads, and returns a refusal in words", async () => {
    const output = setup()
    await flushPromises()
    expect(await output.switchTo(1, [2], true)).toBeNull()
    expect(await output.switchTo(1, [3, 4, 9], true)).toBeNull()
    expect(await output.switchTo(1, null, false)).toBeNull()
    expect(vi.mocked(calls.switchOutput).mock.calls).toEqual([[1, { season: 1, episodes: [2], off: true }],
      [1, { season: 1, episodes: [3, 4, 9], off: true }], [1, { season: 1, off: false }]])
    vi.mocked(calls.switchOutput).mockResolvedValueOnce({ ok: false, text: "Сериал не найден" })
    expect(await output.switchTo(9, null, true)).toBe("Сериал не найден")
    expect(output.failure.value).toBe("Сериал не найден")
  })

  it("names the file of every episode as the plan makes it, and follows every packet of the plan", async () => {
    givePlan(planView({ episodes: [{ ...goal([1, 2], goalVideo("asset", rowRef(8, "v"), "a.mkv")), name: "Show s01e02.mkv" }] }))
    const output = setup()
    await flushPromises()
    expect(output.names.value).toEqual([{ season: 1, episode: 2, part: null, name: "Show s01e02.mkv" }])
    usePlanStore().apply({ ...planView({ episodes: [{ ...goal([1, 2, 1], goalVideo("asset", rowRef(8, "v"), "b.mkv")), name: "Show s01e02 part1.mkv" }] }), references: [] })
    await flushPromises()
    expect(output.names.value).toEqual([{ season: 1, episode: 2, part: 1, name: "Show s01e02 part1.mkv" }])
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
    // The reset is of the level the action stands at: an episode, its voice-over — never the whole series.
    expect(await output.retry(1, 2, "AniStar")).toBeNull()
    expect(calls.resetFailures).toHaveBeenLastCalledWith(1, { season: 1, episode: 2, voice: "AniStar" })
    expect(await output.retry(1, null, "AniStar")).toBeNull()
    expect(calls.resetFailures).toHaveBeenLastCalledWith(1, { season: 1, voice: "AniStar" })
    expect(await output.retryCut(1, 2)).toBeNull()
    expect(calls.resetFailures).toHaveBeenLastCalledWith(1, { season: 1, episode: 2, kind: "cut" })
    expect(await output.retryCut(1, null)).toBeNull()
    expect(calls.resetFailures).toHaveBeenLastCalledWith(1, { season: 1, kind: "cut" })
    // The whole of an episode: what failed for its reference, its voice-overs and its cuts together.
    expect(await output.retryEpisode(1, 2)).toBeNull()
    expect(calls.resetFailures).toHaveBeenLastCalledWith(1, { season: 1, episode: 2 })
    expect(await output.retrySeason(1)).toBeNull()
    expect(calls.resetFailures).toHaveBeenLastCalledWith(1, { season: 1 })
    expect(await output.redo(1, 2, "AniStar")).toBeNull()
    expect(calls.deleteFromPlan).toHaveBeenLastCalledWith(1, { kind: "track", name: "S01E02.mp4", dub: rowRef(8, "other") })
    expect(await output.refetch(1, 2, null)).toBeNull()
    expect(calls.deleteFromPlan).toHaveBeenLastCalledWith(1, { kind: "video", name: "S01E02.mp4" })
    // A track the file does not hold is named by no command.
    expect(await output.redo(1, 2, "Nobody")).toBeNull()
    expect(calls.deleteFromPlan).toHaveBeenCalledTimes(2)
  })

  it("«Пересоздать готовые» of a season makes the track again in every episode whose row a source still gives", async () => {
    givePlan(planView({
      assets: [asset("S01E01.mp4", rowRef(7, "r1"), [assetTrack("AniStar", rowRef(8, "a1"))]),
               asset("S01E02.mp4", rowRef(7, "r2"), [assetTrack("AniStar", rowRef(8, "a2"), false)]),
               asset("S01E03.mp4", rowRef(7, "r3"), [assetTrack("AniStar", rowRef(8, "a3"))])],
      episodes: [1, 2, 3].map((number) => goal([1, number], goalVideo("asset", rowRef(7, `r${number}`), `S01E0${number}.mp4`),
        [goalTrack("AniStar", "reuse", rowRef(8, `a${number}`))])),
    }))
    const output = setup()
    await flushPromises()
    expect(await output.redo(1, null, "AniStar")).toBeNull()
    expect(vi.mocked(calls.deleteFromPlan).mock.calls).toEqual([
      [1, { kind: "track", name: "S01E01.mp4", dub: rowRef(8, "a1") }],
      [1, { kind: "track", name: "S01E03.mp4", dub: rowRef(8, "a3") }]])
  })

  describe("the boxes of a queue", () => {
    const SEASON = { season: 1 }
    const target = (group: string, source: number, player: string) => variantTarget(group, source, player)

    it("shows a press at once and sends the presses of a pause as one command, not one by one", async () => {
      timers()
      const output = setup()
      await flushPromises()
      await output.loadQueues(1)
      const [vibix, kodik] = output.queuesOf(1)!.reference!.rows!
      output.setAllowed(SEASON, "reference", vibix, false)
      expect(output.queuesOf(1)!.reference!.rows!.map((each) => each.allowed)).toEqual([false, true])
      expect(output.queuesOf(1)!.reference!.rows![0].allowed_level).toBe("season")
      output.setAllowed(SEASON, "reference", kodik, false)
      expect(output.heldCount.value).toBe(2)
      expect(output.busy.value).toBe(false) // nothing of the window waits for the core: the next press is taken
      expect(calls.setLevers).not.toHaveBeenCalled()
      expect(calls.setLever).not.toHaveBeenCalled()

      vi.mocked(calls.fetchQueues).mockResolvedValue(ok(seasonQueues(false)))
      await vi.advanceTimersByTimeAsync(QUEUE_PAUSE_MS)
      expect(calls.setLevers).toHaveBeenCalledTimes(1)
      expect(calls.setLevers).toHaveBeenCalledWith(1, [
        setBody(SEASON, "reference.allow", target("Ruchime", 7, "vibix"), false),
        setBody(SEASON, "reference.allow", target("AniStar", 8, "kodik"), false)])
      expect(output.heldCount.value).toBe(0)
      expect(output.failure.value).toBeNull()
      // The queues were read once more after the write, and the boxes show what the core holds.
      expect(calls.fetchQueues).toHaveBeenLastCalledWith(1, 1, null, null)
      expect(output.queuesOf(1)!.reference!.rows!.map((each) => each.allowed)).toEqual([false, false])
    })

    it("sends nothing for a press that brings a box back to what the core holds", async () => {
      timers()
      const output = setup()
      await flushPromises()
      await output.loadQueues(1)
      const [vibix] = output.queuesOf(1)!.reference!.rows!
      output.setAllowed(SEASON, "reference", vibix, false)
      output.setAllowed(SEASON, "reference", { ...vibix, allowed: false }, true)
      expect(output.heldCount.value).toBe(0)
      await vi.advanceTimersByTimeAsync(QUEUE_PAUSE_MS * 2)
      expect(calls.setLevers).not.toHaveBeenCalled()
    })

    it("takes the box of a voice-over group at all in the same command as the boxes of the variants", async () => {
      timers()
      const output = setup()
      await flushPromises()
      await output.loadQueues(1)
      output.setTaken(SEASON, "AniStar", false)
      output.setAllowed(SEASON, "voice", output.queuesOf(1)!.groups![0].rows![0], false)
      expect(output.queuesOf(1)!.groups![0].take).toBe(false)
      expect(output.queuesOf(1)!.groups![0].rows![0].allowed).toBe(false)
      await vi.advanceTimersByTimeAsync(QUEUE_PAUSE_MS)
      expect(calls.setLevers).toHaveBeenCalledWith(1, [
        setBody(SEASON, "voice.take", groupTarget("AniStar"), false),
        setBody(SEASON, "voice.allow", target("AniStar", 8, "kodik"), false)])
    })

    it("goes back to what the core holds, with its words, when the core refuses the command", async () => {
      timers()
      vi.mocked(calls.setLevers).mockResolvedValue({ ok: false, text: "Синхронизация идёт" })
      const output = setup()
      await flushPromises()
      await output.loadQueues(1)
      output.setAllowed(SEASON, "reference", output.queuesOf(1)!.reference!.rows![0], false)
      await vi.advanceTimersByTimeAsync(QUEUE_PAUSE_MS)
      expect(output.failure.value).toBe("Синхронизация идёт")
      expect(output.queuesOf(1)!.reference!.rows!.map((each) => each.allowed)).toEqual([true, true])
    })

    it("sends what is held before any other lever, so the core gets the changes in the order they were made", async () => {
      timers()
      const output = setup()
      await flushPromises()
      await output.loadQueues(1)
      output.setAllowed(SEASON, "reference", output.queuesOf(1)!.reference!.rows![0], false)
      await output.set(SEASON, "voice.take", groupTarget("AniStar"), false)
      expect(calls.setLevers).toHaveBeenCalledTimes(1)
      expect(calls.setLever).toHaveBeenCalledTimes(1)
      expect(vi.mocked(calls.setLevers).mock.invocationCallOrder[0]).toBeLessThan(vi.mocked(calls.setLever).mock.invocationCallOrder[0])
      await vi.advanceTimersByTimeAsync(QUEUE_PAUSE_MS * 2)
      expect(calls.setLevers).toHaveBeenCalledTimes(1) // the pause had nothing left to send
    })

    it("takes a press while a command is on its way and sends it after that one", async () => {
      timers()
      let finish!: (value: Answer<unknown>) => void
      vi.mocked(calls.setLevers).mockImplementationOnce(() => new Promise((resolve) => { finish = resolve }))
      const output = setup()
      await flushPromises()
      await output.loadQueues(1)
      const [vibix, kodik] = output.queuesOf(1)!.reference!.rows!
      output.setAllowed(SEASON, "reference", vibix, false)
      await vi.advanceTimersByTimeAsync(QUEUE_PAUSE_MS)
      expect(output.busy.value).toBe(false)
      output.setAllowed(SEASON, "reference", kodik, false)
      // The box sent stays as the user made it while the core has not answered, the new one is held.
      expect(output.queuesOf(1)!.reference!.rows!.map((each) => each.allowed)).toEqual([false, false])
      await vi.advanceTimersByTimeAsync(QUEUE_PAUSE_MS)
      expect(calls.setLevers).toHaveBeenCalledTimes(1) // it waits for the first
      finish(ok({ success: true }))
      await vi.advanceTimersByTimeAsync(0)
      await flushPromises()
      expect(calls.setLevers).toHaveBeenCalledTimes(2)
      expect(vi.mocked(calls.setLevers).mock.calls[1][1]).toEqual([setBody(SEASON, "reference.allow", target("AniStar", 8, "kodik"), false)])
    })

    it("sends what is held when the window goes", async () => {
      timers()
      const output = setup()
      await flushPromises()
      await output.loadQueues(1)
      output.setAllowed(SEASON, "reference", output.queuesOf(1)!.reference!.rows![0], false)
      mounted.splice(0).forEach((wrapper) => wrapper.unmount())
      await vi.advanceTimersByTimeAsync(0)
      expect(calls.setLevers).toHaveBeenCalledTimes(1)
    })

    it("sends what is held when the page is hidden", async () => {
      timers()
      const output = setup()
      await flushPromises()
      await output.loadQueues(1)
      output.setAllowed(SEASON, "reference", output.queuesOf(1)!.reference!.rows![0], false)
      vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden")
      document.dispatchEvent(new Event("visibilitychange"))
      await vi.advanceTimersByTimeAsync(0)
      expect(calls.setLevers).toHaveBeenCalledTimes(1)
    })
  })
})
