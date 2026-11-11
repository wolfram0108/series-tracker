import { computed, onMounted, ref, shallowRef, watch } from "vue"
import { t } from "../i18n"
import { reasonText } from "../lib/reason"
import {
  draftBody, itemOfSource, playersOf, summaryOf,
  type LayoutRow, type SeriesSource, type SourceItem, type SourceSummary,
} from "../lib/sources"
import { useCompositionStore } from "../stores/composition"
import { useSourceTablesStore } from "../stores/sourceTables"
import { useSSE } from "./useSSE"
import {
  changeSource, createSource, fetchLayout, fetchSources, readPageNow, removeSource, resolveAddress, stopReading,
} from "./sourceCalls"
import { typeAddress } from "./sourceAddress"
import type { DraftPatch } from "./useSourceDrafts"

/**
 * The sources of a stored series for its properties. An action goes to the core at once; what the core makes of
 * it (a new layout, a reading of a page) comes back as events, and the layout event is what reads the sources again.
 * A torrent series has one source and no table: only its address and profile show.
 */
export function useSeriesSources(seriesId: number, kind: "torrent" | "video", initial: SeriesSource[]) {
  const tables = useSourceTablesStore()
  const composition = useCompositionStore()
  const sources = ref<SeriesSource[]>(initial)
  // The answer of the constructor is replaced whole and never changed in place: it is kept as it came (thousands of rows
  // made deeply reactive cost every read of them).
  const summaries = shallowRef<SourceSummary[]>([])
  // The rows the constructor made by the rules of the profiles: the episodes of a player and of its voice-overs are read from them.
  const layout = shallowRef<LayoutRow[]>([])
  // The address the core names the page of a source with in its events, by source id.
  const canonical = ref<Record<number, string>>({})
  const error = ref("")
  const busy = ref(false)

  // A recount of the layout that failed leaves the earlier layout in force; the window says so until the next one arrives.
  const layoutFailure = computed(() => {
    const failed = composition.layoutFailed[seriesId]
    return failed ? t("output.layoutFailed", { reason: reasonText(failed.reason) }) : ""
  })

  // The players of every source are made of the layout and of the choices of the source, not of the news of its page:
  // a packet of a read under way changes the state of its source and recounts no row.
  const players = computed(() => {
    const rows = new Map<number, LayoutRow[]>()
    for (const row of layout.value) {
      const known = rows.get(row.source_id)
      if (known) known.push(row)
      else rows.set(row.source_id, [row])
    }
    return new Map(sources.value.map((source) => [source.id, playersOf(
      summaryOf(summaries.value, source.id), source.players_off ?? [], rows.get(source.id) ?? [])] as const))
  })

  const items = computed<SourceItem[]>(() => sources.value.map((source) => itemOfSource(
    source, summaryOf(summaries.value, source.id), tables.forUrl(canonical.value[source.id] ?? ""), [],
    players.value.get(source.id))))

  async function nameThePage(source: SeriesSource): Promise<void> {
    const answer = await resolveAddress(source.url)
    if (answer.ok && answer.data.canonical_url) canonical.value = { ...canonical.value, [source.id]: answer.data.canonical_url }
  }

  // Only the newest read may write: an older reply that comes late would show sources the core already changed.
  let asked = 0
  async function load(): Promise<void> {
    const ask = ++asked
    const listed = await fetchSources(seriesId)
    if (ask !== asked) return
    if (listed.ok) sources.value = listed.data
    if (kind !== "video") return
    const answer = await fetchLayout(seriesId)
    if (ask !== asked) return
    if (answer.ok) {
      summaries.value = answer.data.sources
      layout.value = answer.data.rows
    }
    await Promise.all(sources.value.filter((source) => !(source.id in canonical.value)).map(nameThePage))
  }

  function refused(text: string): false {
    error.value = text
    return false
  }

  async function act(run: () => Promise<{ ok: true } | { ok: false; text: string }>): Promise<boolean> {
    error.value = ""
    busy.value = true
    try {
      const answer = await run()
      return answer.ok ? true : refused(answer.text)
    } finally {
      busy.value = false
    }
  }

  async function add(raw: string): Promise<boolean> {
    const address = raw.trim()
    if (!address) return false
    error.value = ""
    const typed = await typeAddress(address)
    if (typed.kind === "refused") return refused(typed.text)
    if (typed.kind === "other") return refused(t("sources.add.notSite"))
    const { sourceType, url } = typed
    const done = await act(() => createSource(seriesId, draftBody({
      url, sourceType, profileId: null, vibixSeason: null, playersOff: [],
    })))
    if (done) await load()
    return done
  }

  async function remove(id: string | number): Promise<void> {
    if (await act(() => removeSource(seriesId, Number(id)))) await load()
  }

  /** The fields of a source that the core knows, from the choices of the window. */
  const fieldsOf = (patch: DraftPatch): Record<string, unknown> => ({
    ...(patch.url !== undefined && { url: patch.url }),
    ...(patch.profileId !== undefined && { parser_profile_id: patch.profileId }),
    ...(patch.vibixSeason !== undefined && { vibix_season: patch.vibixSeason }),
    ...(patch.playersOff !== undefined && { players_off: patch.playersOff }),
  })

  async function change(id: string | number, patch: DraftPatch): Promise<void> {
    const source = sources.value.find((each) => each.id === Number(id))
    if (!source) return
    const done = await act(() => changeSource(seriesId, source.id, fieldsOf(patch)))
    if (!done) return
    Object.assign(source, fieldsOf(patch))
    if (patch.url !== undefined) {
      const rest = { ...canonical.value }
      delete rest[source.id]
      canonical.value = rest
      await nameThePage(source)
    }
  }

  /** The page of a source is read now, though its table is not old; the reading and its end come as events. */
  async function reread(id: string | number): Promise<void> {
    const source = sources.value.find((each) => each.id === Number(id))
    if (!source) return
    error.value = ""
    const answer = await readPageNow(source.url)
    if (!answer.ok) error.value = answer.text
  }

  /** The read of the page of a source going now is stopped; its end comes as an event. */
  async function stop(id: string | number): Promise<void> {
    const source = sources.value.find((each) => each.id === Number(id))
    if (!source) return
    error.value = ""
    const answer = await stopReading(source.url)
    if (!answer.ok) error.value = answer.text
  }

  onMounted(() => void load())
  // The layout of the series changed (a summary of a source too): the sources are read again.
  watch(() => composition.layoutChanged[seriesId]?.rev, () => void load())
  // The sources themselves changed (a profile, a disabled player, a season of a catalog): the other window sees it too.
  watch(() => composition.sourcesChanged[seriesId], () => void load())
  // A connection that came back may have missed events: the sources are read again.
  watch(useSSE().connected, (up) => {
    if (up) void load()
  })

  return { items, error, layoutFailure, busy, add, remove, change, reread, stop, reload: load }
}
