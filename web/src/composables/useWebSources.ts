import { computed, onBeforeUnmount, ref } from "vue"
import { api } from "../api/client"
import { blocksAdd, sourceRows, type PlayerReport, type SourcesCoverage, type SourcesLayout } from "../lib/webSources"
import { useSSE } from "./useSSE"

/** The part of a link parse that the sources of a web release need; the form takes the rest itself. */
export interface WebParse {
  canonical_url?: string
  players?: Record<string, PlayerReport>
  has_layout?: boolean
}

async function ask<T>(send: () => Promise<{ data?: unknown }>, failed: T): Promise<T> {
  try {
    return ((await send()).data as T | undefined) ?? failed
  } catch {
    return failed
  }
}

const pageOf = (address: string) => address.split("?")[0]

/**
 * The sources of a web release as the add form and the series properties show them. A provider that reads its
 * sources apart from the parse (a shared catalog, every episode page) answers after it; each part shows when
 * it arrives, and a change of the choice is an address the provider parses again. What the episodes give does
 * not depend on the choice, so it is read once per page.
 */
export function useWebSources() {
  const players = ref<Record<string, PlayerReport>>({})
  const hasLayout = ref(false)
  const layout = ref<SourcesLayout | null>(null)
  const coverage = ref<SourcesCoverage | null>(null)
  const layoutLoading = ref(false)
  const coverageLoading = ref(false)
  const progress = ref<{ current: number; total: number } | null>(null)
  const address = ref("")
  const parsing = ref(false)
  let layoutToken = 0
  let coverageFor = ""

  const stop = useSSE().on("coverage_progress", (data) => {
    const event = data as { url: string; current: number; total: number; active: boolean }
    if (event.url === pageOf(address.value)) progress.value = event.active ? { current: event.current, total: event.total } : null
  })
  onBeforeUnmount(stop)

  function readLayout(url: string): void {
    const mine = ++layoutToken
    layout.value = null
    layoutLoading.value = true
    void ask<SourcesLayout>(() => api.POST("/api/web_video/layout", { body: { url } } as never),
      { sources: [], reason: "failed" }).then((value) => {
      if (mine !== layoutToken) return
      layout.value = value
      layoutLoading.value = false
    })
  }

  function readCoverage(url: string): void {
    const page = pageOf(url)
    if (page === coverageFor) return
    coverageFor = page
    coverage.value = null
    coverageLoading.value = true
    void ask<SourcesCoverage>(() => api.POST("/api/web_video/coverage", { body: { url } } as never),
      { players: {}, reason: "failed" }).then((value) => {
      if (page !== coverageFor) return
      coverage.value = value
      coverageLoading.value = false
      progress.value = null
    })
  }

  function reset(): void {
    layoutToken++
    coverageFor = ""
    players.value = {}
    hasLayout.value = false
    layout.value = null
    coverage.value = null
    layoutLoading.value = false
    coverageLoading.value = false
    progress.value = null
    address.value = ""
  }

  function apply(res: WebParse): void {
    const next = res.canonical_url || ""
    if (pageOf(next) !== coverageFor) reset()
    players.value = res.players ?? {}
    hasLayout.value = !!res.has_layout
    address.value = next
    if (!hasLayout.value) return
    readLayout(next)
    readCoverage(next)
  }

  /** Parse an address and show its sources; the parse result goes to `onParsed` for whatever else needs it. */
  async function pick(url: string, onParsed?: (res: WebParse) => void): Promise<boolean> {
    parsing.value = true
    try {
      const { data, error } = await api.POST("/api/parse_url", { body: { url } } as never)
      if (error || !data) return false
      apply(data as unknown as WebParse)
      onParsed?.(data as unknown as WebParse)
      return true
    } catch {
      return false
    } finally {
      parsing.value = false
    }
  }

  const rows = computed(() => sourceRows({
    players: players.value, layout: layout.value, coverage: coverage.value,
    layoutLoading: layoutLoading.value, coverageLoading: coverageLoading.value,
  }))
  const blocks = computed(() => hasLayout.value && blocksAdd(rows.value))

  return { hasLayout, rows, blocks, progress, address, parsing, apply, pick, reset }
}
