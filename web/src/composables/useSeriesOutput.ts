import { computed, onMounted, ref, watch } from "vue"
import { bySeason, joinEpisodes, resetBody, setBody, type Reference, type Scope } from "../lib/output"
import { useCompositionStore, type ReferencePacket } from "../stores/composition"
import { useVoicesStore } from "../stores/voices"
import { fetchComposition, fetchOutput, resetLever, setLever, type Composition } from "./outputCalls"
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
  const loading = ref(false)
  const failure = ref<string | null>(null)

  const views = computed(() => joinEpisodes(
    composition.value?.rows ?? [],
    references.value,
    composition.value?.settings ?? [],
    voices.forSeries(seriesId),
  ))
  const levels = computed(() => composition.value?.levels ?? [])
  const sources = computed(() => composition.value?.sources ?? [])
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
  }

  // A refusal stays in `failure` until the next action; the words are also returned for the caller.
  async function act(send: () => Promise<Answer<unknown>>): Promise<string | null> {
    failure.value = null
    const answer = await send()
    if (answer.ok) return null
    failure.value = answer.text
    return answer.text
  }

  // The new layout comes back as an event, so nothing is read here after an accepted change.
  const set = (scope: Scope, lever: string, target: string | null, value: unknown): Promise<string | null> =>
    act(() => setLever(seriesId, setBody(scope, lever, target, value)))

  const reset = (scope: Scope, lever?: string, target?: string | null): Promise<string | null> =>
    act(() => resetLever(seriesId, resetBody(scope, lever, target)))

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

  return { composition, references, loading, failure, views, levels, sources, seasons, load, set, reset }
}
