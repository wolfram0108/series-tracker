import { computed, onMounted, ref, watch, type Ref } from "vue"
import { t } from "../i18n"
import {
  bySeason, groupTarget, joinEpisodes, orderValue, queueLevers, resetBody, setBody, sourceMarks, variantTarget,
  type QueueName, type QueueRow, type Reference, type Scope,
} from "../lib/output"
import { reasonText } from "../lib/reason"
import { useCompositionStore, type ReferencePacket } from "../stores/composition"
import { useQueuesStore } from "../stores/queues"
import { useSeriesStore } from "../stores/series"
import { useVoicesStore } from "../stores/voices"
import {
  fetchComposition, fetchMedia, fetchOutput, fetchQueues, fetchRenames, redoDubs, refetchReference, resetFailures, resetLever,
  retryDubs, setLever, setVoiceOrder, switchOutput, syncSeries, type Composition, type MediaFile, type Queues, type RenameRow,
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
  const queues = useQueuesStore()
  const seriesStore = useSeriesStore()
  const composition = ref<Composition | null>(null)
  const references = ref<Reference[]>([])
  // The files of the plan: the status of the video of a reference is read from them.
  const files = ref<MediaFile[]>([])
  // A video series has not been read yet when it is shown first: the window must not say "nothing here" for that moment.
  const loading = ref(kind === "video")
  const failure = ref<string | null>(null)
  // A lever is on its way to the core / a pass over the series is running: the controls that start them wait.
  const busy = ref(false)
  const syncing = ref(false)
  // The names the files would get: asked for by the user, counted by the rules as they are now, so read again with the layout.
  const names = ref<RenameRow[]>([])
  const namesWanted = ref(false)
  const naming = ref(false)

  // The label of a player is a fact of the table of its source, so a voice-over of a player the episode has no line for is named too.
  const labels = computed<Record<string, string>>(() => Object.fromEntries(
    (composition.value?.sources ?? []).flatMap((source) => (source.players ?? [])
      .filter((player) => player.player_label)
      .map((player) => [`${source.source_id}:${player.player}`, player.player_label] as const))))

  const views = computed(() => joinEpisodes(
    composition.value?.rows ?? [],
    references.value,
    composition.value?.settings ?? [],
    voices.forSeries(seriesId),
    composition.value?.availability ?? {},
    files.value,
    labels.value,
  ))
  const levels = computed(() => composition.value?.levels ?? [])
  const sources = computed(() => composition.value?.sources ?? [])
  const specs = computed(() => composition.value?.levers ?? [])
  // A recount of the layout that failed leaves the earlier layout in force; the window says so until the next one arrives.
  const layoutFailure = computed(() => {
    const failed = store.layoutFailed[seriesId]
    return failed ? t("output.layoutFailed", { reason: reasonText(failed.reason) }) : ""
  })
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
    void loadFiles()
    Object.values(store.references[seriesId] ?? {}).forEach((packet) => merged.add(packet))
    const broken = [layout, output].find((answer) => !answer.ok)
    failure.value = broken && !broken.ok ? broken.text : null
    if (packetDuringRead) void load()
    if (namesWanted.value) void loadNames()
  }

  // The files change with the downloads, not with the layout: they are read on their own, the newest ask writing.
  let filesAsked = 0
  async function loadFiles(): Promise<void> {
    const ask = ++filesAsked
    const answer = await fetchMedia(seriesId)
    if (ask === filesAsked && answer.ok) files.value = answer.data
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

  const sync = (): Promise<string | null> => act(() => syncSeries(seriesId), syncing)
  // A change of the series like a lever, not a pass: it starts no sync.
  const retryFailed = (): Promise<string | null> => act(() => resetFailures(seriesId), busy)

  // The order of the voice-overs of a season (the tracks of its containers); the new order comes back as an event.
  const voiceOrders = computed(() => composition.value?.voice_orders ?? [])
  const order = (season: number, labels: string[]): Promise<string | null> =>
    act(() => setVoiceOrder(seriesId, { season, labels }), busy)

  // The number and the colour of each source of the series, as the catalog keeps them (the same on every tab).
  const marks = computed(() => sourceMarks(seriesStore.byId(seriesId)?.sources ?? []))

  // The queues of the levels the window has open, read when one is opened and again with every new layout.
  const levelQueues = ref<Record<string, Queues>>({})
  const queueKey = (season: number | null, episode: number | null): string => JSON.stringify([season, episode])
  const queuesAsked = new Map<string, number>()
  async function loadQueues(season: number | null, episode: number | null = null): Promise<void> {
    const key = queueKey(season, episode)
    const ask = (queuesAsked.get(key) ?? 0) + 1
    queuesAsked.set(key, ask)
    const answer = await fetchQueues(seriesId, season, episode)
    if (queuesAsked.get(key) !== ask) return
    if (answer.ok) levelQueues.value = { ...levelQueues.value, [key]: answer.data }
    else failure.value = answer.text
  }
  const queuesOf = (season: number | null, episode: number | null = null): Queues | undefined =>
    levelQueues.value[queueKey(season, episode)]
  const reloadQueues = (): void => {
    for (const key of Object.keys(levelQueues.value)) {
      const [season, episode] = JSON.parse(key) as [number | null, number | null]
      void loadQueues(season, episode)
    }
  }

  // A queue of a level takes the order the user dragged its rows into, leaves a variant out, or takes a voice-over at all.
  const leversOf = (queue: QueueName) => queueLevers(composition.value?.levers ?? [], queue)
  const setOrder = (scope: Scope, queue: QueueName, group: string | null, rows: readonly QueueRow[]): Promise<string | null> => {
    const lever = leversOf(queue).order
    if (!lever) return Promise.resolve(null)
    return set(scope, lever, queue === "voice" && group !== null ? groupTarget(group) : null, orderValue(queue, rows))
  }
  const setAllowed = (scope: Scope, queue: QueueName, row: QueueRow, allowed: boolean): Promise<string | null> => {
    const lever = leversOf(queue).allow
    if (!lever) return Promise.resolve(null)
    return set(scope, lever, variantTarget(row.group, row.source_id, row.player), allowed)
  }
  const setTaken = (scope: Scope, group: string, taken: boolean): Promise<string | null> => {
    const lever = leversOf("voice").take
    return lever ? set(scope, lever, groupTarget(group), taken) : Promise.resolve(null)
  }
  // "As the level above": the order of the queue on this level goes back to the level above.
  const resetOrder = (scope: Scope, queue: QueueName, group: string | null): Promise<string | null> => {
    const lever = leversOf(queue).order
    if (!lever) return Promise.resolve(null)
    return reset(scope, lever, queue === "voice" && group !== null ? groupTarget(group) : null)
  }

  // The actions on tracks: each is the one command it names, and what it changes comes as events.
  const retry = (season: number, episode: number | null, voiceLabel: string | null): Promise<string | null> =>
    act(() => retryDubs(seriesId, { season, episode, voice_label: voiceLabel }), busy)
  const redo = (season: number, episode: number | null, voiceLabel: string | null): Promise<string | null> =>
    act(() => redoDubs(seriesId, { season, episode, voice_label: voiceLabel }), busy)
  const refetch = (season: number, episode: number, part: number | null): Promise<string | null> =>
    act(() => refetchReference(seriesId, { season, episode, part }), busy)

  if (kind === "video") {
    onMounted(() => void load())
    // The layout moved to a newer revision: the output is read again. The same or an older revision changes nothing.
    watch(() => store.layoutChanged[seriesId]?.rev, () => {
      void load()
      reloadQueues()
    })
    // A packet carries the whole reference of its episode part, so it replaces that entry and needs no read.
    watch(() => store.references[seriesId], (record) => {
      const fresh = Object.values(record ?? {}).filter((packet) => !merged.has(packet))
      if (fresh.length === 0) return
      if (loading.value) packetDuringRead = true
      fresh.forEach((packet) => merged.add(packet))
      references.value = mergePackets(references.value, fresh)
    }, { deep: true })
    // A download began or ended, or the number of downloaded episodes changed: the status of the videos is read again.
    // The progress of a download does not change the length of the queue, so it does not ask.
    watch(() => [queues.downloads.length, seriesStore.byId(seriesId)?.downloaded_episodes_count], () => void loadFiles())
    // A connection that came back may have missed events: the output is read again.
    watch(useSSE().connected, (up) => {
      if (up) void load()
    })
  }

  return { composition, references, loading, failure, busy, syncing, names, namesWanted, naming, views, levels, sources, specs, layoutFailure, seasons, load, set, reset, switchTo,
    sync, retryFailed, showNames, voiceOrders, order, marks, loadQueues, queuesOf, setOrder, setAllowed, setTaken, resetOrder,
    retry, redo, refetch }
}
