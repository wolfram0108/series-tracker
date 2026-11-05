import { computed, ref, watch } from "vue"
import { t } from "../i18n"
import { readEnded, type TableState } from "../lib/sources"
import { useSourceTablesStore } from "../stores/sourceTables"
import { typeAddress } from "./sourceAddress"
import { fetchFilenames, testProfile, type RulesTestReply } from "./rulesTestCalls"
import { useSSE } from "./useSSE"

export type TestLine = NonNullable<RulesTestReply["lines"]>[number]
export type TestState = "idle" | "asking" | "reading" | "ready" | "failed"

interface Page {
  /** The test as it was asked; only `refresh` changes when the core is asked again. */
  address: { url: string }
  /** The address the core names the page with in its events; known after the first answer. */
  canonical: string
  asked: { state: TableState } | null
  /** The news of the page that the last answer already knew; only news after it asks again. */
  handled: ReturnType<ReturnType<typeof useSourceTablesStore>["forUrl"]>
}

/**
 * The test of a profile for its window: a page of a site (or a VK channel) is read by the core and every row comes
 * back with the answer of the profile, or lines the window holds are tried as they are. A page that is still being
 * read is asked again, reading nothing, when the end of its reading comes as an event.
 */
export function useRulesTest(profileId: () => number) {
  const tables = useSourceTablesStore()
  const lines = ref<TestLine[]>([])
  const state = ref<TestState>("idle")
  const reason = ref<string | null>(null)
  const error = ref("")
  // The lines the cards of the last page answer are drawn from: the window offers them for editing.
  const fetched = ref<string[]>([])

  let page: Page | null = null
  // Of several asks in flight only the newest may write.
  let asking = 0

  const progress = computed(() => (page ? tables.forUrl(page.canonical)?.progress ?? null : null))

  function failWith(text: string): void {
    error.value = text
    state.value = "failed"
  }

  async function ask(current: Page, refresh: boolean): Promise<void> {
    const turn = ++asking
    const before = tables.forUrl(current.canonical)
    const answer = await testProfile({
      url: current.address.url, profile_id: profileId(), series_season: null, refresh,
    })
    if (turn !== asking) return
    if (!answer.ok) return failWith(answer.text)
    current.canonical = answer.data.url
    current.asked = { state: answer.data.state }
    current.handled = before
    lines.value = answer.data.lines ?? []
    reason.value = answer.data.reason ?? null
    // A page that is being read, or whose last read failed, answers with the rows of its last complete table: the cards
    // drawn from them and the field of the rows are one set in every state, never cards without their rows.
    if (answer.data.state === "ready" || lines.value.length > 0) fetched.value = lines.value.map((line) => line.line)
    state.value = answer.data.state === "ready" ? "ready" : answer.data.state === "reading" ? "reading" : "failed"
    await settle(current)
  }

  /** A read that ended while the answer was on its way, or after it, asks again once. */
  async function settle(current: Page): Promise<void> {
    if (page === current && readEnded(current.asked, tables.forUrl(current.canonical), current.handled)) {
      await ask(current, false)
    }
  }

  watch(() => tables.pages, () => {
    if (page) void settle(page)
  })

  // A connection that came back may have missed the end of a read: a page still being read is asked again, reading nothing.
  watch(useSSE().connected, (up) => {
    if (up && page?.asked?.state === "reading") void ask(page, false)
  })

  /** Reads the page the address names (a channel of VK with its query, or a page of a site) and tries the profile on its rows.
   *  A channel without a query is not read: the channel is read whole and only the query keeps its videos. */
  async function readPage(address: string, query: string): Promise<void> {
    error.value = ""
    state.value = "asking"
    const typed = await typeAddress(address, query)
    if (typed.kind === "refused") return failWith(typed.text)
    if (typed.kind === "other") return failWith(t("rulesTest.notSite"))
    page = { address: { url: typed.url }, canonical: "", asked: null, handled: null }
    await ask(page, true)
  }

  /** Tries the profile on lines the window holds; no page is read. */
  async function runLines(texts: string[]): Promise<void> {
    const turn = ++asking
    page = null
    error.value = ""
    state.value = "asking"
    const answer = await testProfile({ url: "", profile_id: profileId(), series_season: null, refresh: true, lines: texts })
    if (turn !== asking) return
    if (!answer.ok) return failWith(answer.text)
    lines.value = answer.data.lines ?? []
    reason.value = null
    state.value = "ready"
  }

  /** The file names of the torrents of a stored series; null and the words of the error when they cannot be had. */
  async function loadFilenames(seriesId: number): Promise<string[] | null> {
    error.value = ""
    const answer = await fetchFilenames(seriesId)
    if (answer.ok) return answer.data
    error.value = answer.text
    return null
  }

  return { lines, state, reason, error, progress, fetched, readPage, runLines, loadFilenames }
}
