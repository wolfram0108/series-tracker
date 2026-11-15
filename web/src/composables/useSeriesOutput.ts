import type { SyncScope } from "./syncRun"
import { computed, onMounted, onUnmounted, ref, shallowRef, watch, type Ref } from "vue"
import { t } from "../i18n"
import {
  bySeason, groupTarget, joinEpisodes, orderValue, queueLevers, resetBody, setBody, sourceMarks, variantTarget,
  type QueueName, type QueueRow, type Reference, type Scope,
} from "../lib/output"
import { Gather } from "../lib/gather"
import { QUEUE_PAUSE_MS, bodyOf, changeKey, coreValue, queueKey, scopeKey, withChanges, type QueueChange } from "../lib/queueChange"
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
import { failedOf as failedIn, fileOf, heldFile, planCuts, planDubs, planFiles, planNames, seasonVoiceOrders, takenOf as takenIn, trackOf, type CompilationKey, type FailedAttempt, type HeldFile, type Taken } from "../lib/plan"
import type { SameGroups } from "../lib/same"
import {
  deleteFromPlan, fetchPlan, fetchQueues, resetFailures,
  cancelSync, resetLever, setLever, setLevers, setVoiceOrder, setVoiceTitle, switchOutput, syncSeries, type Composition, type MediaFile,
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
  // A video series has something to show only when its layout and its plan have both been read: the state of every episode is the plan's, so
  // the layout alone would show every episode as having no reference. A torrent series has nothing to wait for.
  const ready = computed(() => kind === "torrent" || (composition.value !== null && plan.forSeries(seriesId) != null))

  // The layout and the plan are read apart, and a read is taken back only by a later read of the same thing: a late reply to an
  // older ask must not roll back, and a read of the plan that a read of the layout overtakes still lands. The words of a failure
  // are those of the layout, else of the plan, until the read that failed is made again.
  let layoutAsked = 0
  let planAsked = 0
  let reading = 0
  const failed: { layout: string | null; plan: string | null } = { layout: null, plan: null }
  const reported = (): void => { failure.value = failed.layout ?? failed.plan }

  async function readLayout(fresh: boolean): Promise<void> {
    const ask = ++layoutAsked
    reading += 1
    loading.value = true
    const answer = await (fresh ? layouts.refresh(seriesId) : layouts.ensure(seriesId))
    reading -= 1
    loading.value = reading > 0
    if (ask !== layoutAsked) return
    failed.layout = answer.ok ? null : answer.text
    reported()
  }

  async function readPlan(): Promise<void> {
    const ask = ++planAsked
    reading += 1
    loading.value = true
    const answer = await fetchPlan(seriesId)
    reading -= 1
    loading.value = reading > 0
    if (ask !== planAsked) return
    if (answer.ok) plan.apply(answer.data)
    failed.plan = answer.ok ? null : answer.text
    reported()
  }

  /** Reads both: when the window opens, when its connection came back, and for the button that tries again. */
  const load = async (fresh = true): Promise<void> => {
    await Promise.all([readLayout(fresh), readPlan()])
  }

  // A refusal stays in `failure` until the next action; the words are also returned for the caller. What the user ticked in
  // the queues and the core does not hold yet goes first, so the core gets the changes in the order they were made.
  async function act(send: () => Promise<Answer<unknown>>, flag: Ref<boolean>): Promise<string | null> {
    failure.value = null
    flag.value = true
    let ok = false
    try {
      if (!gather.idle) await gather.flush()
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

  const sync = (scope?: SyncScope): Promise<string | null> =>
    act(() => scope === undefined ? syncSeries(seriesId) : syncSeries(seriesId, scope), syncing)
  const stop = (): Promise<string | null> => act(() => cancelSync(seriesId), stopping)
  // A change of the series like a lever, not a pass: it starts no sync.
  const retryFailed = (): Promise<string | null> => act(() => resetFailures(seriesId), busy)

  // The failed attempts of one episode, all of them — its reference, its voice-overs, its cuts — as the button of the series
  // resets those of the whole series; the failures of the other episodes stay.
  const retryEpisode = (season: number, episode: number): Promise<string | null> =>
    act(() => resetFailures(seriesId, { season, episode }), busy)

  // The failed attempts of one season, all of them: the levels are the same, the series, a season, an episode.
  const retrySeason = (season: number): Promise<string | null> => act(() => resetFailures(seriesId, { season }), busy)

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
  /** What the file of an episode holds now (lib/plan.ts `heldFile`), for the window of the episode. */
  const heldOf = (season: number, episode: number, part: number | null = null): HeldFile | null => heldFile(plan.forSeries(seriesId), season, episode, part)
  const takenOf = (season: number, episode: number, part: number | null = null): Taken => takenIn(plan.forSeries(seriesId), season, episode, part)
  const failedOf = (season: number, episode: number): FailedAttempt[] => failedIn(plan.forSeries(seriesId), season, episode)
  // The queues as the window shows them: the core's, with the boxes the user changed that the core does not hold yet.
  const shownQueues = computed<Record<string, Queues>>(() => {
    const changes = [...sending.value.values(), ...staged.value.values()]
    if (changes.length === 0) return levelQueues.value
    return Object.fromEntries(Object.entries(levelQueues.value).map(([key, queues]) =>
      [key, withChanges(queues, changes.filter((each) => scopeKey(each.scope) === key))] as const))
  })
  const queuesOf = (season: number | null, episode: number | null = null, compilation: string | null = null): Queues | undefined =>
    shownQueues.value[queueKey(season, episode, compilation)]
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
  // The boxes of a queue are gathered (lib/gather.ts): a press shows at once, the presses of a pause go to the core as one
  // command, and a press that undoes a held one is no change. The core's words of a refusal come as `failure`.
  const staged = shallowRef<ReadonlyMap<string, QueueChange>>(new Map())
  const sending = shallowRef<ReadonlyMap<string, QueueChange>>(new Map())
  const gather = new Gather<QueueChange>({ pause: QUEUE_PAUSE_MS, changed: (held) => { staged.value = held }, send: sendChanges })

  // One batch of boxes: the core takes all or none. What the user sees stands until the queues read after the write land (or
  // the refusal comes), so a box does not jump back to the old value between the answer and the read.
  async function sendChanges(changes: QueueChange[]): Promise<string | null> {
    sending.value = new Map([...sending.value, ...changes.map((each) => [changeKey(each), each] as const)])
    failure.value = null
    try {
      const reply = await setLevers(seriesId, changes.map(bodyOf))
      if (!reply.ok) {
        failure.value = reply.text
        return reply.text
      }
      const open = new Set(Object.keys(levelQueues.value))
      await Promise.all([...new Set(changes.map((each) => each.scope))].filter((scope) => open.has(scopeKey(scope)))
        .map((scope) => loadQueues(scope.season ?? null, scope.episode ?? null, scope.compilation ?? null)))
      return null
    } finally {
      const rest = new Map(sending.value)
      for (const each of changes) if (rest.get(changeKey(each)) === each) rest.delete(changeKey(each))
      sending.value = rest
    }
  }

  // A press: the box the user sees is the held change, else the one on its way, else the core's. A press that brings the
  // box back to what the core holds sends nothing.
  const stage = (change: QueueChange): void => {
    const key = changeKey(change)
    const base = sending.value.get(key)?.value ?? coreValue(levelQueues.value[scopeKey(change.scope)], change)
    if (change.value === base) gather.drop(key)
    else gather.put(key, change)
  }
  const setAllowed = (scope: Scope, queue: QueueName, row: QueueRow, allowed: boolean): void => {
    const lever = leversOf(queue).allow
    if (!lever) return
    stage({ kind: "allow", scope, queue, lever, target: variantTarget(row.group, row.source_id, row.player), value: allowed,
      group: row.group, source_id: row.source_id, player: row.player })
  }
  // The variants of a queue of single videos the user declared equal: the whole value of the lever, none to go back to the level above.
  const setSame = (scope: Scope, queue: QueueName, groups: SameGroups | null): Promise<string | null> => {
    const lever = leversOf(queue).same
    if (!lever) return Promise.resolve(null)
    return groups === null ? reset(scope, lever, null) : set(scope, lever, null, groups)
  }
  const setTaken = (scope: Scope, group: string, taken: boolean): void => {
    const lever = leversOf("voice").take
    if (lever) stage({ kind: "take", scope, queue: "voice", lever, target: groupTarget(group), value: taken, group })
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
    // What is held goes when the window goes, and when the page is hidden or closed: the pause is for the user's next press.
    const hidden = (): void => { if (document.visibilityState === "hidden") void gather.flush() }
    onMounted(() => {
      document.addEventListener("visibilitychange", hidden)
      window.addEventListener("pagehide", hidden)
    })
    onUnmounted(() => {
      document.removeEventListener("visibilitychange", hidden)
      window.removeEventListener("pagehide", hidden)
      void gather.flush()
    })
    // What the window already read is shown as it is; the news since then come as events.
    onMounted(() => void load(false))
    // The layout moved to a newer revision: the layout is read again, and so are the queues. The same or an older revision changes
    // nothing. The plan is the core's push (plan_update), counted once for the change: it is read only if the window has none yet.
    watch(() => store.layoutChanged[seriesId]?.rev, () => {
      void readLayout(true)
      if (plan.forSeries(seriesId) == null) void readPlan()
      reloadQueues()
    })
    // A connection that came back may have missed events: the output is read again.
    watch(useSSE().connected, (up) => {
      if (up) void load()
    })
  }

  const heldCount = computed(() => staged.value.size)

  return { composition, references, loading, ready, failure, busy, heldCount, answer, syncing, stopping, stop, names, views, levels, sources, specs, layoutFailure, seasons, load, set, reset, switchTo,
    sync, retryFailed, retrySeason, retryEpisode, voiceOrders, order, rename, marks, loadQueues, queuesOf, heldOf, takenOf, failedOf, setOrder, setAllowed, setSame, setTaken, resetOrder,
    retry, redo, refetch, compilations, cuts, member, takeCompilation, retryCut, labels }
}
