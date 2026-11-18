import { beforeEach, describe, expect, it } from "vitest"
import { createPinia, setActivePinia } from "pinia"
import { useSourceTablesStore } from "./sourceTables"

const PAGE = "https://1.ruchime.org/release/test"

describe("source tables store", () => {
  beforeEach(() => setActivePinia(createPinia()))

  it("follows a read from its progress to its end by the canonical address", () => {
    const store = useSourceTablesStore()
    store.applyProgress({ url: PAGE, phase: "episodes", current: 1, total: 20 })
    expect(store.forUrl(PAGE)).toMatchObject({ state: "reading", progress: { phase: "episodes", current: 1, total: 20 } })
    store.applyReady({ url: PAGE, read_at: "2026-11-01T10:00:00Z", fresh_until: null })
    expect(store.forUrl(PAGE)).toMatchObject({ state: "ready", progress: null, readAt: "2026-11-01T10:00:00Z" })
    expect(store.forUrl("https://other")).toBeNull()
  })

  it("keeps the reason of a failed read and the time of the last complete one", () => {
    const store = useSourceTablesStore()
    store.applyReady({ url: PAGE, read_at: "2026-11-01T10:00:00Z", fresh_until: null })
    store.applyProgress({ url: PAGE, phase: "players", current: 0, total: 0 })
    store.applyFailed({ url: PAGE, reason: '{"code":"read_timeout","seconds":900}' })
    expect(store.forUrl(PAGE)).toMatchObject({ state: "failed", reason: '{"code":"read_timeout","seconds":900}', readAt: "2026-11-01T10:00:00Z" })
  })

  it("replaces the news object at every event, so a watcher sees each one", () => {
    const store = useSourceTablesStore()
    store.applyProgress({ url: PAGE, phase: "episodes", current: 1, total: 2 })
    const first = store.forUrl(PAGE)
    store.applyProgress({ url: PAGE, phase: "episodes", current: 2, total: 2 })
    expect(store.forUrl(PAGE)).not.toBe(first)
  })

  it("forgets the news after a break of the connection", () => {
    const store = useSourceTablesStore()
    store.applyProgress({ url: PAGE, phase: "episodes", current: 1, total: 2 })
    store.reset()
    expect(store.forUrl(PAGE)).toBeNull()
  })
})
