import { defineStore } from "pinia"
import { fetchComposition, type Composition } from "../composables/outputCalls"
import type { Answer } from "../composables/sourceCalls"
import { merged, type LayoutNews } from "../lib/layoutParts"
import { useCompositionStore } from "./composition"
import { keyed } from "./keyed"

/**
 * The layout of a series as its window holds it: read from the core once for every part of the window (the sources, the
 * output), megabytes for a long series. The parts hold it while they are shown; when the last lets go, it is dropped.
 * It is a projection the news of the core keep current: a change reads again only the part it touched (the summaries of
 * the sources, the levers of some episodes) and puts it into what is held; the whole is read when the window opens and
 * when it asks for it (a connection that came back, the button that tries again).
 */
export const useLayoutStore = defineStore("layout", () => {
  const { all: bySeries, put, drop } = keyed<Composition>()
  // The read under way of a series, and the one asked for after it began: at most one of each.
  const flights = new Map<number, Promise<Answer<Composition>>>()
  const queued = new Map<number, Promise<Answer<Composition>>>()
  const holders = new Map<number, number>()

  const forSeries = (seriesId: number): Composition | undefined => bySeries[seriesId]

  const composition = useCompositionStore()

  /** A read of the layout: of the part the news name when the window holds the layout, else of the whole. */
  function start(seriesId: number, news?: LayoutNews): Promise<Answer<Composition>> {
    const held = bySeries[seriesId]
    const part = held && news ? news.part : "whole"
    const episodes = part === "levers" && news?.episodes ? [...news.episodes] : []
    const flight: Promise<Answer<Composition>> = fetchComposition(seriesId, part, episodes).then((answer) => {
      if (flights.get(seriesId) === flight) flights.delete(seriesId)
      if (!answer.ok) {
        if (news) composition.giveBack(seriesId, news)
        return answer
      }
      const now = bySeries[seriesId]
      const layout = part === "whole" || !now ? answer.data : merged(now, answer.data, news?.episodes ?? null)
      if (holders.get(seriesId)) put(seriesId, layout)
      return { ok: true as const, data: layout }
    })
    flights.set(seriesId, flight)
    return flight
  }

  function queue(seriesId: number, read: () => Promise<Answer<Composition>>): Promise<Answer<Composition>> {
    const waiting = queued.get(seriesId)
    if (waiting) return waiting
    const before = flights.get(seriesId)
    const next = Promise.resolve(before).then(() => {
      queued.delete(seriesId)
      return read()
    })
    queued.set(seriesId, next)
    return next
  }

  /** The layout as the core has it now. Asks made together are one read; an ask that comes while a read is under way is
   *  answered by a read begun after it: what the read under way brings may be older than the news behind the ask. */
  function refresh(seriesId: number): Promise<Answer<Composition>> {
    return queue(seriesId, () => {
      composition.takeNews(seriesId)  // the whole holds every change so far
      return start(seriesId)
    })
  }

  /** The layout after the news of the core: the part they touched is read and put into what is held (the whole when
   *  nothing is held yet). News that come while a read is under way are taken by the read begun after it. */
  function catchUp(seriesId: number): Promise<Answer<Composition>> {
    return queue(seriesId, () => {
      const news = composition.takeNews(seriesId)
      const held = bySeries[seriesId]
      if (held && !news) return Promise.resolve({ ok: true as const, data: held })
      return start(seriesId, news)
    })
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

  return { bySeries, forSeries, refresh, catchUp, ensure, hold }
})
