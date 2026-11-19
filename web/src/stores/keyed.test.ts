import { beforeEach, describe, expect, it } from "vitest"
import { computed, isReactive, ref, watch } from "vue"
import { createPinia, setActivePinia } from "pinia"
import { keyed } from "./keyed"
import { useCompositionStore } from "./composition"
import { usePlanStore } from "./plan"
import { useSeriesReadsStore } from "./seriesReads"
import { useSourceTablesStore } from "./sourceTables"
import type { PlannerView } from "../lib/plan"

const plan = (series: number): PlannerView =>
  ({ series_id: series, running: false, references: [], assets: [], episodes: [], failures: [], problems: [], commands: [] } as unknown as PlannerView)

describe("keyed", () => {
  it("wakes the readers of the key that was written and no reader of another key", () => {
    const { all, put, drop, clear } = keyed<{ n: number }>()
    let ones = 0, twos = 0, listed = 0
    const one = computed(() => (ones += 1, all[1]?.n ?? null))
    const two = computed(() => (twos += 1, all[2]?.n ?? null))
    const keys = computed(() => (listed += 1, Object.keys(all).length))
    expect([one.value, two.value, keys.value]).toEqual([null, null, 0])
    put(1, { n: 5 })
    expect([one.value, two.value, keys.value, ones, twos, listed]).toEqual([5, null, 1, 2, 1, 2])
    put(1, { n: 6 })
    expect([one.value, two.value, keys.value, ones, twos, listed]).toEqual([6, null, 1, 3, 1, 2])
    drop(1)
    expect([one.value, two.value, keys.value, twos]).toEqual([null, null, 0, 1])
    put(2, { n: 1 })
    clear()
    expect([two.value, keys.value]).toEqual([null, 0])
  })

  it("keeps a packet as it came: the same object where it is read and where a screen stored it", () => {
    const { all, put } = keyed<{ n: number }>()
    const packet = { n: 1 }
    put("page", packet)
    const stored = ref<{ handled: { n: number } | null }>({ handled: null })
    stored.value.handled = all.page
    expect(all.page).toBe(packet)
    expect(stored.value.handled).toBe(all.page)
    expect(isReactive(stored.value.handled)).toBe(false)
  })
})

describe("the stores the core pushes by key", () => {
  beforeEach(() => setActivePinia(createPinia()))

  it("leave the window of one series alone on the news of another series or page", () => {
    const plans = usePlanStore()
    const tables = useSourceTablesStore()
    const reads = useSeriesReadsStore()
    const layout = useCompositionStore()
    let counted = 0
    // What a window of series 46 reads: its plan, the page of its source, its reads, the revision of its layout.
    const mine = computed(() => (counted += 1, [
      plans.forSeries(46)?.series_id ?? null, tables.forUrl("https://site/mine")?.state ?? null,
      reads.forSeries(46).length, layout.layoutChanged[46]?.rev ?? null, layout.layoutFailed[46]?.reason ?? null,
      layout.sourcesChanged[46] ?? 0,
    ]))
    expect(mine.value).toEqual([null, null, 0, null, null, 0])
    plans.apply(plan(40))
    tables.applyProgress({ url: "https://site/other", phase: "players", current: 1, total: 9 })
    tables.applyReady({ url: "https://site/other", read_at: "2026-11-01T10:00:00Z", fresh_until: null })
    reads.apply({ series_id: 40, source_id: 1, url: "https://site/other", phase: "players", current: 1, total: 9 })
    reads.apply({ series_id: 40, source_id: 1, url: "https://site/other", active: false })
    layout.applyChanged({ series_id: 40, rev: 2, episodes: [], summary_only: false, part: "whole" })
    layout.applyFailed({ series_id: 40, reason: "{}" })
    layout.applySourcesChanged({ series_id: 40 } as never)
    expect(mine.value).toEqual([null, null, 0, null, null, 0])
    expect(counted).toBe(1)
    plans.apply(plan(46))
    tables.applyProgress({ url: "https://site/mine", phase: "players", current: 1, total: 9 })
    expect(mine.value.slice(0, 2)).toEqual([46, "reading"])
    expect(counted).toBe(2)
  })

  it("tell a screen that watches every page of each packet of any page", async () => {
    const tables = useSourceTablesStore()
    let seen = 0
    watch(tables.pages, () => { seen += 1 }, { flush: "sync" })
    tables.applyProgress({ url: "https://site/a", phase: "players", current: 1, total: 2 })
    tables.applyProgress({ url: "https://site/a", phase: "players", current: 2, total: 2 })
    tables.applyReady({ url: "https://site/b", read_at: "2026-11-01T10:00:00Z", fresh_until: null })
    expect(seen).toBe(3)
  })
})
