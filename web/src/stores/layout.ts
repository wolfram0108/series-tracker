import { defineStore } from "pinia"
import { fetchComposition, type Composition } from "../composables/outputCalls"
import type { Answer } from "../composables/sourceCalls"
import { keyed } from "./keyed"

/**
 * The layout of a series as its window holds it: read from the core once for every part of the window (the sources, the
 * output), megabytes for a long series. The parts hold it while they are shown; when the last lets go, it is dropped.
 */
export const useLayoutStore = defineStore("layout", () => {
  const { all: bySeries, put, drop } = keyed<Composition>()
  // The read under way of a series, and the one asked for after it began: at most one of each.
  const flights = new Map<number, Promise<Answer<Composition>>>()
  const queued = new Map<number, Promise<Answer<Composition>>>()
  const holders = new Map<number, number>()

  const forSeries = (seriesId: number): Composition | undefined => bySeries[seriesId]

  function start(seriesId: number): Promise<Answer<Composition>> {
    const flight: Promise<Answer<Composition>> = fetchComposition(seriesId).then((answer) => {
      if (flights.get(seriesId) === flight) flights.delete(seriesId)
      if (answer.ok && holders.get(seriesId)) put(seriesId, answer.data)
      return answer
    })
    flights.set(seriesId, flight)
    return flight
  }

  /** The layout as the core has it now. Asks made together are one read; an ask that comes while a read is under way is
   *  answered by a read begun after it: what the read under way brings may be older than the news behind the ask. */
  function refresh(seriesId: number): Promise<Answer<Composition>> {
    const waiting = queued.get(seriesId)
    if (waiting) return waiting
    const before = flights.get(seriesId)
    const next = Promise.resolve(before).then(() => {
      queued.delete(seriesId)
      return start(seriesId)
    })
    queued.set(seriesId, next)
    return next
  }

  /** The layout the window already holds, or the read that brings it (the one under way, when there is one). */
  function ensure(seriesId: number): Promise<Answer<Composition>> {
    const held = bySeries[seriesId]
    if (held) return Promise.resolve({ ok: true, data: held })
    return queued.get(seriesId) ?? flights.get(seriesId) ?? refresh(seriesId)
  }

  /** A part of a window that shows the layout of the series; the call it returns lets the layout go. */
  function hold(seriesId: number): () => void {
    holders.set(seriesId, (holders.get(seriesId) ?? 0) + 1)
    let held = true
    return () => {
      if (!held) return
      held = false
      const left = (holders.get(seriesId) ?? 1) - 1
      if (left > 0) return void holders.set(seriesId, left)
      holders.delete(seriesId)
      drop(seriesId)
    }
  }

  return { bySeries, forSeries, refresh, ensure, hold }
})
