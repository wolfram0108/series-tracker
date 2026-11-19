import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { nextTick, ref, watch } from "vue"

// A stand-in for the browser's EventSource: the test hands it the frames the gateway would write.
class FakeSource {
  static last: FakeSource | null = null
  url: string
  listeners = new Map<string, (event: MessageEvent) => void>()
  readyState = 0
  onopen: (() => void) | null = null
  onerror: (() => void) | null = null
  constructor(url: string) {
    this.url = url
    FakeSource.last = this
  }
  addEventListener(type: string, listener: (event: MessageEvent) => void) {
    this.listeners.set(type, listener)
  }
  close() {}
  frame(type: string, data: string) {
    this.listeners.get(type)?.({ data } as MessageEvent)
  }
}

// A stream that comes back asks the server whether it serves another build: the page of a test has no build to ask about.
vi.mock("../lib/build", () => ({ reloadIfRebuilt: vi.fn(async () => {}) }))

async function freshSSE() {
  vi.resetModules()
  const { useSSE } = await import("./useSSE")
  const sse = useSSE()
  sse.connect()
  return { sse, source: FakeSource.last! }
}

describe("useSSE", () => {
  beforeEach(() => vi.stubGlobal("EventSource", FakeSource))
  afterEach(() => vi.unstubAllGlobals())

  it("reads the stream by turns and hands the events of a turn to their handlers in order", async () => {
    const { sse, source } = await freshSSE()
    expect(source.url).toBe("/api/stream?frames=batch")
    const got: unknown[] = []
    sse.on("plan_update", (data) => got.push(["reference", data]))
    sse.on("composition_changed", (data) => got.push(["composition", data]))
    source.frame("batch", JSON.stringify([
      { event: "plan_update", data: { episode: 1 } },
      { event: "an_event_nobody_listens_to", data: {} },
      { event: "composition_changed", data: { rev: 2 } },
      { event: "plan_update", data: { episode: 2 } },
    ]))
    expect(got).toEqual([["reference", { episode: 1 }], ["composition", { rev: 2 }], ["reference", { episode: 2 }]])
  })

  it("loses only the event whose handler throws, not the rest of the turn", async () => {
    const { sse, source } = await freshSSE()
    const errors = vi.spyOn(console, "error").mockImplementation(() => {})
    const got: unknown[] = []
    sse.on("plan_update", (data) => {
      if ((data as { episode: number }).episode === 1) throw new Error("broken")
      got.push(data)
    })
    source.frame("batch", JSON.stringify([
      { event: "plan_update", data: { episode: 1 } },
      { event: "plan_update", data: { episode: 2 } },
    ]))
    expect(got).toEqual([{ episode: 2 }])
    expect(errors).toHaveBeenCalledOnce()
  })

  it("skips a broken frame", async () => {
    const { sse, source } = await freshSSE()
    const got: unknown[] = []
    sse.on("plan_update", (data) => got.push(data))
    source.frame("batch", "{not json")
    source.frame("batch", JSON.stringify({ event: "plan_update", data: {} }))
    expect(got).toEqual([])
  })

  it("counts a screen once for a burst of a turn, not once per event", async () => {
    const { sse, source } = await freshSSE()
    const references = ref<number[]>([])
    sse.on("plan_update", (data) => {
      references.value = [...references.value, (data as { episode: number }).episode]
    })
    // A screen follows its store as a watcher does: it runs at the flush after the task, once for what changed in it.
    const counted: number[] = []
    watch(references, (now) => counted.push(now.length))
    source.frame("batch", JSON.stringify(Array.from({ length: 215 }, (_, episode) => (
      { event: "plan_update", data: { episode } }))))
    await nextTick()
    expect(counted).toEqual([215])
  })

  it("tells that the stream is lost until it is open again, and an end the user asked for is no loss", async () => {
    const { sse, source } = await freshSSE()
    source.onopen!()
    expect(sse.lost.value).toBe(false)
    source.onerror!() // the browser mends it by itself
    expect(sse.lost.value).toBe(true)
    source.onopen!()
    expect(sse.lost.value).toBe(false)
    source.onerror!()
    sse.disconnect()
    expect(sse.lost.value).toBe(false)
  })

  it("asks whether the server serves another build when the stream comes back after a loss, not at the first open", async () => {
    const reload = vi.fn(async () => {})
    vi.doMock("../lib/build", () => ({ reloadIfRebuilt: reload }))
    const { source } = await freshSSE()
    source.onopen!()
    expect(reload).not.toHaveBeenCalled()
    source.onerror!()
    source.onopen!()
    expect(reload).toHaveBeenCalledTimes(1)
    vi.doUnmock("../lib/build")
  })

  it("opens a stream the server closed for good again, once the owner of the login allows it, at growing spaces", async () => {
    vi.useFakeTimers()
    const { sse, source } = await freshSSE()
    const allowed = vi.fn(async () => true)
    sse.retryOnlyIf(allowed)
    source.readyState = 2
    source.onerror!()
    await vi.advanceTimersByTimeAsync(0)
    expect(allowed).toHaveBeenCalledOnce()
    await vi.advanceTimersByTimeAsync(2999)
    expect(FakeSource.last).toBe(source)
    await vi.advanceTimersByTimeAsync(1)
    const again = FakeSource.last!
    expect(again).not.toBe(source)
    again.readyState = 2
    again.onerror!()
    await vi.advanceTimersByTimeAsync(5999)
    expect(FakeSource.last).toBe(again)
    await vi.advanceTimersByTimeAsync(1)
    expect(FakeSource.last).not.toBe(again)
    vi.useRealTimers()
  })

  it("does not open it again where the login must come first", async () => {
    vi.useFakeTimers()
    const { sse, source } = await freshSSE()
    sse.retryOnlyIf(async () => false)
    source.readyState = 2
    source.onerror!()
    await vi.advanceTimersByTimeAsync(60_000)
    expect(FakeSource.last).toBe(source)
    vi.useRealTimers()
  })
})
