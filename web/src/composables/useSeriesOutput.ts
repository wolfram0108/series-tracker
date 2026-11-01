import { computed, onMounted, ref, watch, type Ref } from "vue"
import { bySeason, joinEpisodes, resetBody, setBody, type Reference, type Scope } from "../lib/output"
import { useCompositionStore, type ReferencePacket } from "../stores/composition"
import { useVoicesStore } from "../stores/voices"
import {
  adoptSeries, fetchComposition, fetchOutput, fetchRenames, reprocessSeries, resetLever, setLever, switchOutput, syncSeries,
  type Composition, type RenameRow,
} from "./outputCalls"
import type { Answer } from "./sourceCalls"
import { useSSE } from "./useSSE"

// A reference is one entry per (season, episode, part); a packet replaces the entry of its own key.
const keyOf = (reference: Pick<Reference, "season" | "episode" | "part">): string =>
  JSON.stringify([reference.season, reference.episode, reference.part ?? null])

function mergePackets(current: Reference[], packets: ReferencePacket[]): Reference[] {
  const byKey = new Map(current.map((reference) => [keyOf(reference), reference] as const))
  for (const packet of packets) {
    const { series_id: _seriesId, ...reference } = packet
    byKey.set(keyOf(reference), reference)
  }
  return [...byKey.values()]
}

/**
 * The output of a video series as its window reads it: episodes joined from the layout, references, levers and
 * voice-overs. A torrent series has no output and loads nothing. Changes come as events: a new layout reads the
 * output again, a reference packet is merged in, and a connection that came back reads it again.
 */
export function useSeriesOutput(seriesId: number, kind: "torrent" | "video") {
  const store = useCompositionStore()
  const voices = useVoicesStore()
  const composition = ref<Composition | null>(null)
  const references = ref<Reference[]>([])
  // A video series has not been read yet when it is shown first: the window must not say "nothing here" for that moment.
  const loading = ref(kind === "video")
  const failure = ref<string | null>(null)
  // A lever is on its way to the core / a pass over the series is running: the controls that start them wait.
  const busy = ref(false)
  const syncing = ref(false)
  const reprocessing = ref(false)
  const adopting = ref(false)
  // The names the files would get: asked for by the user, counted by the rules as they are now, so read again with the layout.
  const names = ref<RenameRow[]>([])
  const namesWanted = ref(false)
  const naming = ref(false)

  const views = computed(() => joinEpisodes(
    composition.value?.rows ?? [],
    references.value,
    composition.value?.settings ?? [],
    voices.forSeries(seriesId),
  ))
  const levels = computed(() => composition.value?.levels ?? [])
  const sources = computed(() => composition.value?.sources ?? [])
  const specs = computed(() => composition.value?.levers ?? [])
  const seasons = computed(() => bySeason(views.value))

  // Of several reads in flight only the one asked last may write: a late reply to an older ask must not roll back.
  let asked = 0
  // A packet that comes while a read is in flight may be older than that read's reply: the output is read once more.
  let packetDuringRead = false
  // A packet is merged once: the packets already in the store when a read answers are older than that answer.
  const merged = new WeakSet<object>()
  async function load(): Promise<void> {
    const ask = ++asked
    loading.value = true
    packetDuringRead = false
    const [layout, output] = await Promise.all([fetchComposition(seriesId), fetchOutput(seriesId)])
    if (ask !== asked) return
    loading.value = false
    if (layout.ok) composition.value = layout.data
    if (output.ok) references.value = output.data
    Object.values(store.references[seriesId] ?? {}).forEach((packet) => merged.add(packet))
    const broken = [layout, output].find((answer) => !answer.ok)
    failure.value = broken && !broken.ok ? broken.text : null
    if (packetDuringRead) void load()
    if (namesWanted.value) void loadNames()
  }

  let namesAsked = 0
  async function loadNames(): Promise<void> {
    const ask = ++namesAsked
    naming.value = true
    const answer = await fetchRenames(seriesId)
    if (ask !== namesAsked) return
    naming.value = false
    if (answer.ok) names.value = answer.data.preview ?? []
    else failure.value = answer.text
  }

  function showNames(on: boolean): Promise<void> {
    namesWanted.value = on
    if (on) return loadNames()
    namesAsked += 1
    naming.value = false
    names.value = []
    return Promise.resolve()
  }

  // A refusal stays in `failure` until the next action; the words are also returned for the caller.
  async function act(send: () => Promise<Answer<unknown>>, flag: Ref<boolean>): Promise<string | null> {
    failure.value = null
    flag.value = true
    try {
      const answer = await send()
      if (answer.ok) return null
      failure.value = answer.text
      return answer.text
    } finally {
      flag.value = false
    }
  }

  // The new layout comes back as an event, so nothing is read here after an accepted change.
  const set = (scope: Scope, lever: string, target: string | null, value: unknown): Promise<string | null> =>
    act(() => setLever(seriesId, setBody(scope, lever, target, value)), busy)

  const reset = (scope: Scope, lever?: string, target?: string | null): Promise<string | null> =>
    act(() => resetLever(seriesId, resetBody(scope, lever, target)), busy)

  // An episode, or a whole season (no episode in the scope), off or on; the references that follow come as events.
  const switchTo = (season: number, episode: number | null, off: boolean): Promise<string | null> =>
    act(() => switchOutput(seriesId, { season, ...(episode === null ? {} : { episode }), off }), busy)

  const reprocess = (): Promise<string | null> => act(() => reprocessSeries(seriesId), reprocessing)

  const adopt = (): Promise<string | null> => act(() => adoptSeries(seriesId), adopting)

  const sync = (): Promise<string | null> => act(() => syncSeries(seriesId), syncing)

  if (kind === "video") {
    onMounted(() => void load())
    // The layout moved to a newer revision: the output is read again. The same or an older revision changes nothing.
    watch(() => store.layoutChanged[seriesId]?.rev, () => void load())
    // A packet carries the whole reference of its episode part, so it replaces that entry and needs no read.
    watch(() => store.references[seriesId], (record) => {
      const fresh = Object.values(record ?? {}).filter((packet) => !merged.has(packet))
      if (fresh.length === 0) return
      if (loading.value) packetDuringRead = true
      fresh.forEach((packet) => merged.add(packet))
      references.value = mergePackets(references.value, fresh)
    }, { deep: true })
    // A connection that came back may have missed events: the output is read again.
    watch(useSSE().connected, (up) => {
      if (up) void load()
    })
  }

  return { composition, references, loading, failure, busy, syncing, reprocessing, adopting, names, namesWanted, naming, views, levels, sources, specs, seasons, load, set, reset, switchTo,
    sync, reprocess, adopt, showNames }
}
