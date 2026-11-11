import { beforeEach, describe, expect, it, vi } from "vitest"
import { createPinia, setActivePinia } from "pinia"
import { useLayoutStore } from "./layout"
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
})
