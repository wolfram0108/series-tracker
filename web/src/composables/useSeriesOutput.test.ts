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
import { useCompositionStore, type ReferencePacket } from "../stores/composition"
import { useVoicesStore, type VoiceDubEvent } from "../stores/voices"
import { groupTarget, LEVER, resetBody, setBody, type Reference, type Row } from "../lib/output"
import type { SourceSummary } from "../lib/sources"

vi.mock("./outputCalls")

const PAGE = "https://1.ruchime.org/release/test"
const ok = <T>(data: T): Answer<T> => ({ ok: true, data })

const row = (over: Partial<Row> = {}): Row => ({
  series_id: 1, source_id: 7, row_key: "row", excluded: false, season: 1, episode_start: 2, episode_end: 2,
  voice_label: "Ruchime", voice_name: "Ruchime", player: "vibix", player_label: "", group: "", text: "",
  heights: [1080], state: "playable", link: "", ...over,
})
const reference = (over: Partial<Reference> = {}): Reference => ({
  season: 1, episode: 2, part: null, mark: "by_level", source_id: 7, row_key: "row", player: "vibix",
  voice_group: "Ruchime", height: 1080, reason: null, ...over,
})
const packet = (over: Partial<ReferencePacket> = {}): ReferencePacket => ({
  season: 1, episode: 2, part: null, mark: "own", source_id: 8, row_key: "other", player: "kodik",
  voice_group: "AniStar", height: 720, reason: null, series_id: 1, ...over,
})
const summary: SourceSummary = {
  source_id: 7, url: PAGE, status: "no_profile",
  players: [{ player: "vk", player_label: "VK", rows: 3, groups: 0, heights: [720], voices: [] }],
}
const composition = (over: Partial<Composition> = {}): Composition => ({
  series_id: 1, rev: 1, rows: [row()], sources: [summary],
  levels: [{ season: 1, levers: [{ lever: LEVER.take, target: groupTarget("Ruchime"), value: false, level: "season" }] }],
  settings: [{ season: 1, episode: 2, levers: [{ lever: LEVER.take, target: groupTarget("Ruchime"), value: true, level: "episode" }] }],
  ...over,
})
const dubEvent = (over: Partial<VoiceDubEvent> = {}): VoiceDubEvent => ({
  series_id: 1, job_id: null, file: null, season: 1, episode: 2, voice_key: "ruchime", voice_label: "Ruchime",
  status: "ready", state: "align_queued", hint: "ready", detail: null, epoch: 1, rev: 1, ...over,
})

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
  vi.mocked(calls.fetchOutput).mockReset().mockResolvedValue(ok([reference()]))
  vi.mocked(calls.setLever).mockReset().mockResolvedValue(ok({ success: true }))
  vi.mocked(calls.resetLever).mockReset().mockResolvedValue(ok({ success: true }))
  vi.mocked(calls.syncSeries).mockReset().mockResolvedValue(ok({ success: true }))
})

afterEach(() => mounted.splice(0).forEach((wrapper) => wrapper.unmount()))

describe("useSeriesOutput", () => {
  it("reads the layout and the references of a video series and joins them into episodes", async () => {
    useVoicesStore().apply(dubEvent())
    const output = setup()
    await flushPromises()
    expect(calls.fetchComposition).toHaveBeenCalledWith(1)
    expect(calls.fetchOutput).toHaveBeenCalledWith(1)
    expect(output.views.value).toEqual([{
      season: 1, episode: 2, references: [reference()], rows: [row()],
      levers: [{ lever: LEVER.take, target: groupTarget("Ruchime"), value: true, level: "episode" }],
      dubs: [expect.objectContaining({ voice_label: "Ruchime", state: "align_queued" })],
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
    expect(calls.fetchOutput).not.toHaveBeenCalled()
    expect(output.composition.value).toBeNull()
    expect(output.references.value).toEqual([])
    expect(output.views.value).toEqual([])
  })

  it("merges a reference packet into the reference of its episode, without a new read and without the series", async () => {
    const output = setup()
    await flushPromises()
    const store = useCompositionStore()
    store.applyReference(packet())
    await flushPromises()
    expect(output.references.value).toEqual([
      reference({ mark: "own", source_id: 8, row_key: "other", player: "kodik", voice_group: "AniStar", height: 720 }),
    ])
    expect(output.views.value[0].references.map((each) => each.mark)).toEqual(["own"])
    store.applyReference(packet({ episode: 3 }))
    await flushPromises()
    expect(output.references.value.map((each) => [each.episode, each.mark])).toEqual([[2, "own"], [3, "own"]])
    store.applyReference(packet({ series_id: 2 }))
    await flushPromises()
    expect(output.references.value).toHaveLength(2)
    expect(calls.fetchOutput).toHaveBeenCalledTimes(1)
  })

  it("merges a packet once, so that a later read is not rolled back by a packet it already took into account", async () => {
    const store = useCompositionStore()
    const output = setup()
    await flushPromises()
    store.applyReference(packet())
    await flushPromises()
    expect(output.references.value.find((each) => each.episode === 2)?.mark).toBe("own")
    vi.mocked(calls.fetchOutput).mockResolvedValue(ok([reference({ mark: "by_level" })]))
    store.applyChanged({ series_id: 1, rev: 2, episodes: [], summary_only: false })
    await flushPromises()
    store.applyReference(packet({ episode: 3 }))
    await flushPromises()
    expect(output.references.value.find((each) => each.episode === 2)?.mark).toBe("by_level")
    expect(output.references.value.find((each) => each.episode === 3)?.mark).toBe("own")
  })

  it("reads the output once more when a packet came while a read was in flight, and not otherwise", async () => {
    let release!: (value: Answer<Reference[]>) => void
    vi.mocked(calls.fetchOutput).mockReset()
      .mockImplementationOnce(() => new Promise((resolve) => { release = resolve }))
      .mockResolvedValue(ok([reference({ mark: "own", source_id: 8 })]))
    setup()
    await flushPromises()
    useCompositionStore().applyReference(packet())
    await flushPromises()
    release(ok([reference()]))
    await flushPromises()
    expect(calls.fetchOutput).toHaveBeenCalledTimes(2)
    useCompositionStore().applyReference(packet({ episode: 3 }))
    await flushPromises()
    expect(calls.fetchOutput).toHaveBeenCalledTimes(2)
  })

  it("reads the output again when the layout gets a newer revision, and not for the same one or for another series", async () => {
    setup()
    await flushPromises()
    const before = vi.mocked(calls.fetchComposition).mock.calls.length
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
    expect(calls.fetchOutput).toHaveBeenCalledTimes(before + 2)
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

  it("shows the words of a refused read and keeps the reference that did come", async () => {
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
    expect(await output.set({ season: 1, episode: 2 }, LEVER.take, target, false)).toBeNull()
    expect(calls.setLever).toHaveBeenCalledWith(1, setBody({ season: 1, episode: 2 }, LEVER.take, target, false))
    expect(vi.mocked(calls.setLever).mock.calls[0][1]).toStrictEqual({
      season: 1, episode: 2, lever: "voice.take", target: "group:Ruchime", value: false,
    })

    expect(await output.reset({ season: 1 }, LEVER.take)).toBeNull()
    expect(vi.mocked(calls.resetLever).mock.calls[0][1]).toStrictEqual(resetBody({ season: 1 }, LEVER.take))
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
    const pending = output.set({ season: 1 }, LEVER.take, groupTarget("Ruchime"), false)
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
  })

  it("reads the output again when the connection comes back", async () => {
    setup()
    await flushPromises()
    const before = vi.mocked(calls.fetchComposition).mock.calls.length
    useSSE().connected.value = true
    await flushPromises()
    expect(calls.fetchComposition).toHaveBeenCalledTimes(before + 1)
    expect(calls.fetchOutput).toHaveBeenCalledTimes(before + 1)
  })
})
