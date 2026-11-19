import { beforeEach, describe, expect, it, vi } from "vitest"
import { createPinia, setActivePinia } from "pinia"
import { useLayoutStore } from "./layout"
import { useCompositionStore } from "./composition"
import * as calls from "../composables/outputCalls"
import type { Composition } from "../composables/outputCalls"

vi.mock("../composables/outputCalls")

const layout = (rev: number) => ({ series_id: 1, rev, rows: [], sources: [] }) as unknown as Composition
const ok = (data: Composition) => ({ ok: true as const, data })
const settled = () => new Promise((done) => setTimeout(done, 0))

describe("the layout of a series in its window", () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.mocked(calls.fetchComposition).mockReset().mockImplementation(async () => ok(layout(1)))
  })

  it("is read once for every part of the window that opens with it, and what is held is asked for no more", async () => {
    const store = useLayoutStore()
    const release = [store.hold(1), store.hold(1)]
    const answers = await Promise.all([store.ensure(1), store.ensure(1)])
    expect(calls.fetchComposition).toHaveBeenCalledTimes(1)
    expect(answers.map((answer) => answer.ok && answer.data.rev)).toEqual([1, 1])
    expect(store.forSeries(1)?.rev).toBe(1)
    await store.ensure(1)
    expect(calls.fetchComposition).toHaveBeenCalledTimes(1)
    release.forEach((each) => each())
  })

  it("is read once for the news every part asks about together, and again for news that came during a read", async () => {
    const store = useLayoutStore()
    const release = store.hold(1)
    let give!: (answer: ReturnType<typeof ok>) => void
    vi.mocked(calls.fetchComposition).mockReset()
      .mockImplementationOnce(() => new Promise((resolve) => { give = resolve }))
      .mockImplementation(async () => ok(layout(3)))
    const first = [store.refresh(1), store.refresh(1)]
    await settled()
    expect(calls.fetchComposition).toHaveBeenCalledTimes(1)
    // News while the read is under way: one more read after it, whoever asks, and an ask without news shares it.
    const later = [store.refresh(1), store.refresh(1), store.ensure(1)]
    give(ok(layout(2)))
    expect((await Promise.all(first)).map((answer) => answer.ok && answer.data.rev)).toEqual([2, 2])
    expect((await Promise.all(later)).map((answer) => answer.ok && answer.data.rev)).toEqual([3, 3, 3])
    expect(calls.fetchComposition).toHaveBeenCalledTimes(2)
    expect(store.forSeries(1)?.rev).toBe(3)
    release()
  })

  it("is dropped when the last part of the window lets it go, and a read that ends after that leaves nothing", async () => {
    const store = useLayoutStore()
    const [one, two] = [store.hold(1), store.hold(1)]
    await store.ensure(1)
    one()
    one()
    expect(store.forSeries(1)?.rev).toBe(1)
    const late = store.refresh(1)
    two()
    expect(store.forSeries(1)).toBeUndefined()
    await late
    expect(store.forSeries(1)).toBeUndefined()
  })

  it("keeps the layout it holds when a read is refused", async () => {
    const store = useLayoutStore()
    const release = store.hold(1)
    await store.ensure(1)
    vi.mocked(calls.fetchComposition).mockResolvedValueOnce({ ok: false, text: "Нет связи с сервером" })
    expect(await store.refresh(1)).toEqual({ ok: false, text: "Нет связи с сервером" })
    expect(store.forSeries(1)?.rev).toBe(1)
    release()
  })

  const settingsOf = (episodes: number[], value: string) =>
    episodes.map((episode) => ({ season: 1, episode, levers: [{ lever: value }] })) as unknown as Composition["settings"]
  const held = (rev: number) => ({ series_id: 1, rev, part: "whole", rows: [{ row_key: "r" }], sources: [{ source_id: 1 }],
    settings: settingsOf([1, 2, 3], "old"), levels: [], compilations: [], voice_orders: [], voice_titles: {} }) as unknown as Composition
  const told = (rev: number, part: string, episodes: number[] = []) => useCompositionStore().applyChanged(
    { series_id: 1, rev, part, summary_only: part === "sources", episodes: episodes.map((episode) => ({ season: 1, episode })) } as never)

  it("reads after a change of levers only the levers of the episodes it touched and keeps the rows it holds", async () => {
    const store = useLayoutStore()
    const release = store.hold(1)
    vi.mocked(calls.fetchComposition).mockReset().mockResolvedValueOnce(ok(held(1)))
    await store.ensure(1)
    vi.mocked(calls.fetchComposition).mockResolvedValueOnce(ok({ series_id: 1, rev: 2, part: "levers",
      settings: settingsOf([2], "new"), sources: [{ source_id: 1, state: "x" }] } as unknown as Composition))
    told(2, "levers", [2])
    const answer = await store.catchUp(1)
    expect(vi.mocked(calls.fetchComposition).mock.calls.at(-1)).toEqual([1, "levers", ["1:2"]])
    const now = store.forSeries(1)!
    expect(answer.ok && answer.data).toBe(now)
    expect(now.rev).toBe(2)
    expect(now.rows).toEqual([{ row_key: "r" }])
    expect((now.settings ?? []).map((each) => [each.episode, (each.levers?.[0] as unknown as { lever: string }).lever]))
      .toEqual([[1, "old"], [2, "new"], [3, "old"]])
    release()
  })

  it("takes the news of every change since the last read at once, the widest part of them, and the whole for a change of rows", async () => {
    const store = useLayoutStore()
    const release = store.hold(1)
    vi.mocked(calls.fetchComposition).mockReset().mockResolvedValue(ok(held(1)))
    await store.ensure(1)
    told(2, "sources")
    told(3, "levers", [1])
    told(4, "levers", [3])
    await store.catchUp(1)
    expect(vi.mocked(calls.fetchComposition).mock.calls.at(-1)).toEqual([1, "levers", ["1:1", "1:3"]])
    told(5, "levers", [1])
    told(6, "whole")
    await store.catchUp(1)
    expect(vi.mocked(calls.fetchComposition).mock.calls.at(-1)).toEqual([1, "whole", []])
    await store.catchUp(1)  // no news since: nothing is read
    expect(calls.fetchComposition).toHaveBeenCalledTimes(3)
    release()
  })

  it("gives the news back when the read of a part is refused, so the next read takes them", async () => {
    const store = useLayoutStore()
    const release = store.hold(1)
    vi.mocked(calls.fetchComposition).mockReset().mockResolvedValueOnce(ok(held(1)))
      .mockResolvedValueOnce({ ok: false, text: "Нет связи с сервером" }).mockResolvedValue(ok(held(3)))
    await store.ensure(1)
    told(2, "levers", [2])
    expect((await store.catchUp(1)).ok).toBe(false)
    told(3, "levers", [3])
    await store.catchUp(1)
    expect(vi.mocked(calls.fetchComposition).mock.calls.at(-1)).toEqual([1, "levers", ["1:2", "1:3"]])
    release()
  })
})
