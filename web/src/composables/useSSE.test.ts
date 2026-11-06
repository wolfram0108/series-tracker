import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { nextTick, ref, watch } from "vue"

// A stand-in for the browser's EventSource: the test hands it the frames the gateway would write.
class FakeSource {
  static last: FakeSource | null = null
  url: string
  listeners = new Map<string, (event: MessageEvent) => void>()
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
    sse.on("reference_changed", (data) => got.push(["reference", data]))
    sse.on("composition_changed", (data) => got.push(["composition", data]))
    source.frame("batch", JSON.stringify([
      { event: "reference_changed", data: { episode: 1 } },
      { event: "an_event_nobody_listens_to", data: {} },
      { event: "composition_changed", data: { rev: 2 } },
      { event: "reference_changed", data: { episode: 2 } },
    ]))
    expect(got).toEqual([["reference", { episode: 1 }], ["composition", { rev: 2 }], ["reference", { episode: 2 }]])
  })

  it("loses only the event whose handler throws, not the rest of the turn", async () => {
    const { sse, source } = await freshSSE()
    const errors = vi.spyOn(console, "error").mockImplementation(() => {})
    const got: unknown[] = []
    sse.on("reference_changed", (data) => {
      if ((data as { episode: number }).episode === 1) throw new Error("broken")
      got.push(data)
    })
    source.frame("batch", JSON.stringify([
      { event: "reference_changed", data: { episode: 1 } },
      { event: "reference_changed", data: { episode: 2 } },
    ]))
    expect(got).toEqual([{ episode: 2 }])
    expect(errors).toHaveBeenCalledOnce()
  })

  it("skips a broken frame", async () => {
    const { sse, source } = await freshSSE()
    const got: unknown[] = []
    sse.on("reference_changed", (data) => got.push(data))
    source.frame("batch", "{not json")
    source.frame("batch", JSON.stringify({ event: "reference_changed", data: {} }))
    expect(got).toEqual([])
  })

  it("counts a screen once for a burst of a turn, not once per event", async () => {
    const { sse, source } = await freshSSE()
    const references = ref<number[]>([])
    sse.on("reference_changed", (data) => {
      references.value = [...references.value, (data as { episode: number }).episode]
    })
    // A screen follows its store as a watcher does: it runs at the flush after the task, once for what changed in it.
    const counted: number[] = []
    watch(references, (now) => counted.push(now.length))
    source.frame("batch", JSON.stringify(Array.from({ length: 215 }, (_, episode) => (
      { event: "reference_changed", data: { episode } }))))
    await nextTick()
    expect(counted).toEqual([215])
  })
})
