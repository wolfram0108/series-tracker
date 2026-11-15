import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { Gather } from "./gather"

beforeEach(() => vi.useFakeTimers())
afterEach(() => vi.useRealTimers())

function gatherer(send: (changes: string[]) => Promise<string | null> = async () => null) {
  const sent = vi.fn(send)
  const told: string[][] = []
  const gather = new Gather<string>({ pause: 3000, send: sent, changed: (held) => told.push([...held.keys()]) })
  return { gather, sent, told }
}

describe("Gather", () => {
  it("sends what was held as one batch once the user has been quiet for the pause", async () => {
    const { gather, sent } = gatherer()
    gather.put("a", "A")
    await vi.advanceTimersByTimeAsync(2000)
    gather.put("b", "B")
    await vi.advanceTimersByTimeAsync(2999)
    expect(sent).not.toHaveBeenCalled() // every change restarts the pause
    await vi.advanceTimersByTimeAsync(1)
    expect(sent).toHaveBeenCalledTimes(1)
    expect(sent).toHaveBeenCalledWith(["A", "B"])
    expect(gather.size).toBe(0)
  })

  it("keeps the later change of one key and the order the keys were first held in", async () => {
    const { gather, sent } = gatherer()
    gather.put("a", "A1")
    gather.put("b", "B")
    gather.put("a", "A2")
    await gather.flush()
    expect(sent).toHaveBeenCalledWith(["A2", "B"])
  })

  it("sends nothing for a change that was taken back, and stops waiting when nothing is held", async () => {
    const { gather, sent } = gatherer()
    gather.put("a", "A")
    gather.drop("a")
    await vi.advanceTimersByTimeAsync(10_000)
    expect(sent).not.toHaveBeenCalled()
    gather.put("a", "A")
    gather.put("b", "B")
    gather.drop("a")
    await vi.advanceTimersByTimeAsync(3000)
    expect(sent).toHaveBeenCalledWith(["B"])
  })

  it("sends at once on a flush, ends the pause, and answers with the words of the core", async () => {
    const { gather, sent } = gatherer(async () => "Серия идёт")
    gather.put("a", "A")
    expect(await gather.flush()).toBe("Серия идёт")
    await vi.advanceTimersByTimeAsync(10_000)
    expect(sent).toHaveBeenCalledTimes(1)
  })

  it("sends a batch after the one before it, in the order they were made, and a refused one stops no later one", async () => {
    const order: string[] = []
    const gates: Array<() => void> = []
    const { gather } = gatherer((changes) => new Promise((resolve) => {
      order.push(`start ${changes.join("")}`)
      gates.push(() => { order.push(`end ${changes.join("")}`); resolve(changes[0] === "A" ? "нет" : null) })
    }))
    gather.put("a", "A")
    const first = gather.flush()
    gather.put("b", "B")
    const second = gather.flush()
    await vi.advanceTimersByTimeAsync(0)
    expect(order).toEqual(["start A"]) // the second waits for the first
    gates[0]()
    expect(await first).toBe("нет")
    await vi.advanceTimersByTimeAsync(0)
    gates[1]()
    expect(await second).toBeNull()
    expect(order).toEqual(["start A", "end A", "start B", "end B"])
  })

  it("answers a flush with nothing held when the sends on their way have ended", async () => {
    let end!: () => void
    const { gather } = gatherer(() => new Promise((resolve) => { end = () => resolve(null) }))
    gather.put("a", "A")
    void gather.flush()
    let done = false
    void gather.flush().then(() => { done = true })
    await vi.advanceTimersByTimeAsync(0)
    expect(done).toBe(false)
    end()
    await vi.advanceTimersByTimeAsync(0)
    expect(done).toBe(true)
  })

  it("is idle when nothing is held and nothing is on its way", async () => {
    let end!: () => void
    const { gather } = gatherer(() => new Promise((resolve) => { end = () => resolve(null) }))
    expect(gather.idle).toBe(true)
    gather.put("a", "A")
    expect(gather.idle).toBe(false)
    const sent = gather.flush()
    expect(gather.idle).toBe(false) // held no more, but on its way
    await vi.advanceTimersByTimeAsync(0)
    end()
    await sent
    await vi.advanceTimersByTimeAsync(0)
    expect(gather.idle).toBe(true)
  })

  it("tells the held keys with every change of them", async () => {
    const { gather, told } = gatherer()
    gather.put("a", "A")
    gather.put("b", "B")
    gather.drop("a")
    await gather.flush()
    expect(told).toEqual([["a"], ["a", "b"], ["b"], []])
  })

  it("goes on after a send that threw", async () => {
    const calls: string[][] = []
    const gather = new Gather<string>({
      pause: 10,
      send: async (changes) => {
        calls.push(changes)
        if (calls.length === 1) throw new Error("boom")
        return null
      },
    })
    gather.put("a", "A")
    await expect(gather.flush()).rejects.toThrow("boom")
    gather.put("b", "B")
    expect(await gather.flush()).toBeNull()
    expect(calls).toEqual([["A"], ["B"]])
  })
})
