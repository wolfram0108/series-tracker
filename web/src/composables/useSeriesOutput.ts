import { computed, onMounted, onUnmounted, ref, watch, type Ref } from "vue"
import { t } from "../i18n"
import {
  bySeason, groupTarget, joinEpisodes, orderValue, queueLevers, resetBody, setBody, sourceMarks, variantTarget,
  type QueueName, type QueueRow, type Reference, type Scope,
} from "../lib/output"
import { nextAnswer, type ChangeAnswer } from "../lib/changeAnswer"
import { sharing } from "../lib/sharing"
import { reasonText } from "../lib/reason"
import { useCompositionStore } from "../stores/composition"
import { useQueuesStore } from "../stores/queues"
import { useVoicesStore } from "../stores/voices"
import { liveEntries, liveFrom } from "../lib/live"
import { useSeriesStore } from "../stores/series"
import { usePlanStore } from "../stores/plan"
import { useLayoutStore } from "../stores/layout"
import { fileOf, planCuts, planDubs, planFiles, planNames, seasonVoiceOrders, trackOf, type CompilationKey } from "../lib/plan"
import {
  deleteFromPlan, fetchPlan, fetchQueues, resetFailures,
  cancelSync, resetLever, setLever, setVoiceOrder, setVoiceTitle, switchOutput, syncSeries, type Composition, type MediaFile,
  type Queues,
} from "./outputCalls"
import type { Answer } from "./sourceCalls"
import { useSSE } from "./useSSE"

/**
 * The output of a video series as its window reads it: episodes joined from the layout, references, levers and
 * voice-overs. A torrent series has no output and loads nothing. Changes come as events: a new layout reads the
 * output again, a reference packet is merged in, and a connection that came back reads it again.
 */
export function useSeriesOutput(seriesId: number, kind: "torrent" | "video") {
  const store = useCompositionStore()
  const plan = usePlanStore()
  const seriesStore = useSeriesStore()
  // The layout is the window's one copy (stores/layout.ts): the sources of the window read the same one.
  const layouts = useLayoutStore()
  const queues = useQueuesStore()
  const voices = useVoicesStore()
  // What the agents do for the episodes of the series now (lib/live.ts). The queues push the figures of the work with every
  // packet; the entries are kept as text, so the episodes are counted again only when a state of some work changed.
  const liveNow = computed(() => JSON.stringify(liveEntries(seriesId, queues.downloads, queues.conform?.tasks ?? [], queues.pack, voices.dubName)))
  const live = computed(() => liveFrom(JSON.parse(liveNow.value)))
  const composition = computed<Composition | null>(() => layouts.forSeries(seriesId) ?? null)
  // The reference of every episode, and why, is the plan's: it comes with every packet of the plan of the series.
  const references = computed<Reference[]>(() => plan.forSeries(seriesId)?.references ?? [])
  // The files of the plan: the status of the video of a reference is read from them; the plan comes with every push.
  const files = sharing<MediaFile[]>(() => planFiles(plan.forSeries(seriesId), compilationKey.value, live.value))
  // The compilations of the layout by their row: the plan names a compilation by its row, the window by its key.
  const compilationKey = computed<CompilationKey>(() => {
    const keys = new Map((composition.value?.compilations ?? []).map((each) =>
      [`${each.ref.source_id}:${each.ref.row_key}`, each.ref.key] as const))
    return (row) => keys.get(`${row.source_id}:${row.row_key}`) ?? JSON.stringify(row)
  })
  // The order of the voice-overs of every season: a voice-over of it the plan cannot get for an episode is absent there.
  // The order of the tracks of every season: the layout's, and the voice-overs the files hold that no source gives any more.
  const voiceOrders = sharing(() => seasonVoiceOrders(composition.value?.voice_orders ?? [], plan.forSeries(seriesId),
    composition.value?.voice_titles ?? {}))
  const seasonVoices = computed<Record<number, string[]>>(() => Object.fromEntries(
    voiceOrders.value.map((each) => [each.season, (each.voices ?? []).map((voice) => voice.label)] as const)))
  // A video series has not been read yet when it is shown first: the window must not say "nothing here" for that moment.
  const loading = ref(kind === "video")
  const failure = ref<string | null>(null)
  // A lever is on its way to the core / a pass over the series is running: the controls that start them wait.
  const busy = ref(false)
  const syncing = ref(false)
  const stopping = ref(false)
  // The answer of the core to every change the window sent: a list that shows a dragged order waits on it (OrderList).
  const answer = ref<ChangeAnswer>({ n: 0, ok: true })
  // The name of the file of every episode is the plan's: it comes with every packet of the plan of the series.
  const names = sharing(() => planNames(plan.forSeries(seriesId)))

  // The label of a player is a fact of the table of its source, so a voice-over of a player the episode has no line for is named too.
  const labels = computed<Record<string, string>>(() => Object.fromEntries(
    (composition.value?.sources ?? []).flatMap((source) => (source.players ?? [])
      .filter((player) => player.player_label)
      .map((player) => [`${source.source_id}:${player.player}`, player.player_label] as const))))

  // A packet of the plan is the whole plan: the episodes it left as they were stay the objects they were, and their cards
  // are not drawn again (lib/sharing.ts).
  const views = sharing(() => joinEpisodes(
    composition.value?.rows ?? [],
    references.value,
    composition.value?.settings ?? [],
    planDubs(plan.forSeries(seriesId), seasonVoices.value, live.value),
    composition.value?.availability ?? {},
    files.value,
    labels.value,
    live.value.packs,
  ))
  const levels = computed(() => composition.value?.levels ?? [])
  const sources = computed(() => composition.value?.sources ?? [])
  const specs = computed(() => composition.value?.levers ?? [])
  // A recount of the layout that failed leaves the earlier layout in force; the window says so until the next one arrives.
  const layoutFailure = computed(() => {
    const failed = store.layoutFailed[seriesId]
    return failed ? t("output.layoutFailed", { reason: reasonText(failed.reason) }) : ""
  })
  const seasons = sharing(() => bySeason(views.value))

  // Of several reads in flight only the one asked last may write: a late reply to an older ask must not roll back.
  let asked = 0
  async function load(fresh = true): Promise<void> {
    const ask = ++asked
    loading.value = true
    const [layout, planned] = await Promise.all([
      fresh ? layouts.refresh(seriesId) : layouts.ensure(seriesId), fetchPlan(seriesId)])
    if (ask !== asked) return
    loading.value = false
    if (planned.ok) plan.apply(planned.data)
    const broken = [layout, planned].find((answer) => !answer.ok)
    failure.value = broken && !broken.ok ? broken.text : null
  }

  // A refusal stays in `failure` until the next action; the words are also returned for the caller.
  async function act(send: () => Promise<Answer<unknown>>, flag: Ref<boolean>): Promise<string | null> {
    failure.value = null
    flag.value = true
    let ok = false
    try {
      const reply = await send()
      ok = reply.ok
      if (reply.ok) return null
      failure.value = reply.text
      return reply.text
    } finally {
      answer.value = nextAnswer(answer.value, ok)
      flag.value = false
    }
  }

  // The new layout comes back as an event, so nothing is read here after an accepted change.
  const set = (scope: Scope, lever: string, target: string | null, value: unknown): Promise<string | null> =>
    act(() => setLever(seriesId, setBody(scope, lever, target, value)), busy)

  const reset = (scope: Scope, lever?: string, target?: string | null, queue?: QueueName): Promise<string | null> =>
    act(() => resetLever(seriesId, resetBody(scope, lever, target, queue)), busy)

  // Episodes of a season, one or many in one command, or the whole season (no episodes), off or on; the references that
  // follow come as events.
  const switchTo = (season: number, episodes: number[] | null, off: boolean): Promise<string | null> =>
    act(() => switchOutput(seriesId, { season, ...(episodes === null ? {} : { episodes }), off }), busy)

  const sync = (): Promise<string | null> => act(() => syncSeries(seriesId), syncing)
  const stop = (): Promise<string | null> => act(() => cancelSync(seriesId), stopping)
  // A change of the series like a lever, not a pass: it starts no sync.
  const retryFailed = (): Promise<string | null> => act(() => resetFailures(seriesId), busy)

  // The order of the voice-overs of a season (the tracks of its containers); the new order comes back as an event.
  const order = (season: number, labels: string[]): Promise<string | null> =>
    act(() => setVoiceOrder(seriesId, { season, labels }), busy)

  // The user's name of the track of a voice-over in every container of the series ("": the name of its group).
  const rename = (label: string, title: string): Promise<string | null> =>
    act(() => setVoiceTitle(seriesId, { label, title }), busy)

  // The number and the colour of each source of the series, as the catalog keeps them (the same on every tab).
  const marks = computed(() => sourceMarks(seriesStore.byId(seriesId)?.sources ?? []))

  // The queues of the levels the window has open, read when one is opened and again with every new layout.
  const levelQueues = ref<Record<string, Queues>>({})
  const queueKey = (season: number | null, episode: number | null, compilation: string | null = null): string =>
    JSON.stringify([season, episode, compilation])
  const queuesAsked = new Map<string, number>()
  async function loadQueues(season: number | null, episode: number | null = null, compilation: string | null = null): Promise<void> {
    const key = queueKey(season, episode, compilation)
    const ask = (queuesAsked.get(key) ?? 0) + 1
    queuesAsked.set(key, ask)
    const answer = await fetchQueues(seriesId, season, episode, compilation)
    if (queuesAsked.get(key) !== ask) return
    if (answer.ok) levelQueues.value = { ...levelQueues.value, [key]: answer.data }
    else failure.value = answer.text
  }
  const queuesOf = (season: number | null, episode: number | null = null, compilation: string | null = null): Queues | undefined =>
    levelQueues.value[queueKey(season, episode, compilation)]
  const reloadQueues = (): void => {
    for (const key of Object.keys(levelQueues.value)) {
      const [season, episode, compilation] = JSON.parse(key) as [number | null, number | null, string | null]
      void loadQueues(season, episode, compilation ?? null)
    }
  }

  // Stage 12: the compilations of every season, the cuts of the episodes (read once, then the pushes of the core), the
  // compilation an episode stands in (the user's word, or "" for none) and the switch of a whole compilation.
  const compilations = computed(() => composition.value?.compilations ?? [])
  const cuts = computed(() => planCuts(plan.forSeries(seriesId), compilationKey.value))
  const member = (season: number, episode: number, key: string): Promise<string | null> =>
    set({ season, episode }, "compilation.member", null, key)
  const takeCompilation = (season: number, key: string, taken: boolean): Promise<string | null> =>
    taken ? reset({ season, compilation: key }, "compilation.take", null) : set({ season, compilation: key }, "compilation.take", null, false)
  // A cut that failed is tried again once its failure is reset (the plan passes over a failure until then): the cuts of the
  // episode, or of the season; the failures of the other levels stay.
  const retryCut = (season: number, episode: number | null): Promise<string | null> =>
    act(() => resetFailures(seriesId, { season, ...(episode === null ? {} : { episode }), kind: "cut" }), busy)

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
  // "As the level above": the order of the queue on this level goes back to the level above; the markup reference goes
  // back whole, its order and its boxes, so that with nothing above it follows the reference again (stage 14).
  const resetOrder = (scope: Scope, queue: QueueName, group: string | null): Promise<string | null> => {
    if (queue === "markup") return reset(scope, undefined, undefined, "markup")
    const lever = leversOf(queue).order
    if (!lever) return Promise.resolve(null)
    return reset(scope, lever, queue === "voice" && group !== null ? groupTarget(group) : null)
  }

  // The actions on tracks: each is the one command it names, and what it changes comes as events.
  // A failed voice-over is tried again once its failure is reset: the voice-over of the episode (or of the season, with no
  // episode) — the failures of the others stay, so the references the plan chose by them stay chosen. «Удалить» of a track or
  // of the video takes it out of the file at the next sync, and a row a source still gives is downloaded again.
  const retry = (season: number, episode: number | null, voiceLabel: string | null): Promise<string | null> =>
    act(() => resetFailures(seriesId, { season, ...(episode === null ? {} : { episode }),
      ...(voiceLabel === null ? {} : { voice: voiceLabel }) }), busy)
  // «Пересоздать» of one episode is the command of its track; of a season («Пересоздать готовые»), the command of the
  // track in every episode whose row a source still gives — a track no source gives has nothing to be made again from.
  const redo = async (season: number, episode: number | null, voiceLabel: string | null): Promise<string | null> => {
    const view = plan.forSeries(seriesId)
    if (!voiceLabel) return null
    const episodes = episode !== null ? [episode]
      : (view?.episodes ?? []).filter((goal) => goal.slot[0] === season && goal.slot.length === 2).map((goal) => goal.slot[1])
    const targets = episodes.flatMap((number) => {
      const name = fileOf(view, season, number, null)
      const track = name ? trackOf(view, name, voiceLabel) : null
      if (!name || !track?.row || (episode === null && track.online === false)) return []
      return [{ name, dub: track.row }]
    })
    if (!targets.length) return null
    return act(async () => {
      let reply: Answer<unknown> = { ok: true, data: null }
      for (const target of targets) {
        reply = await deleteFromPlan(seriesId, { kind: "track", ...target })
        if (!reply.ok) break
      }
      return reply
    }, busy)
  }
  const refetch = async (season: number, episode: number, part: number | null): Promise<string | null> => {
    const name = fileOf(plan.forSeries(seriesId), season, episode, part)
    return name ? act(() => deleteFromPlan(seriesId, { kind: "video", name }), busy) : null
  }

  if (kind === "video") {
    onUnmounted(layouts.hold(seriesId))
    // What the window already read is shown as it is; the news since then come as events.
    onMounted(() => void load(false))
    // The layout moved to a newer revision: the output is read again. The same or an older revision changes nothing.
    watch(() => store.layoutChanged[seriesId]?.rev, () => {
      void load()
      reloadQueues()
    })
    // A connection that came back may have missed events: the output is read again.
    watch(useSSE().connected, (up) => {
      if (up) void load()
    })
  }

  return { composition, references, loading, failure, busy, answer, syncing, stopping, stop, names, views, levels, sources, specs, layoutFailure, seasons, load, set, reset, switchTo,
    sync, retryFailed, voiceOrders, order, rename, marks, loadQueues, queuesOf, setOrder, setAllowed, setTaken, resetOrder,
    retry, redo, refetch, compilations, cuts, member, takeCompilation, retryCut, labels }
}
