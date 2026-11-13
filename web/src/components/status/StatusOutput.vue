<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, provide, ref, watch } from "vue"
import Button from "primevue/button"
import { useSeriesOutput } from "../../composables/useSeriesOutput"
import { t } from "../../i18n"
import {
  allowedIn, episodeStatus, isDeviation, isOff, trackCells, type EpisodeStatus, type EpisodeView, type QueueName, type QueueRow,
  type Scope,
} from "../../lib/output"
import { CHANGE_ANSWER } from "../../lib/changeAnswer"
import { HELD, holdLever } from "../../lib/lever"
import { NO_SELECTION, keyOf, picked, selectedOf, switchesOff, type Pick, type Selection } from "../../lib/selection"
import { pageHost, progressText } from "../../lib/sources"
import { useSeriesStore } from "../../stores/series"
import { useSeriesReadsStore } from "../../stores/seriesReads"
import { badgeOf, cardStatus, cutOf, cutOverride, cutsBySlot, heldCut, takes, type CompilationView, type CutState } from "../../lib/compilations"
import CompilationBlock from "./output/CompilationBlock.vue"
import LevelSettings from "./output/LevelSettings.vue"
import PlanPending from "./output/PlanPending.vue"
import SeasonBlock from "./output/SeasonBlock.vue"
import SourceMark from "./output/SourceMark.vue"

// The "Output" tab of a video series: its episodes season by season as cards (or tiles), each opened below its row with
// its tracks and its queues; the settings of a season and of the series are the same queues for all their episodes. The
// data is the core's: every change goes to it and comes back as events.
const props = defineProps<{ seriesId: number; seriesName: string }>()

const output = useSeriesOutput(props.seriesId, "video")
const series = useSeriesStore()
// A sync of the series holds the levers for real, and they fade; a change of the window on its way holds them without a fade
// (lib/lever.ts), so that a press does not make every control of the tab blink.
const busy = computed(() => !!series.byId(props.seriesId)?.is_busy)
const waiting = computed(() => output.busy.value)
provide(CHANGE_ANSWER, output.answer)
const hold = (event: Event): void => { if (waiting.value) holdLever(event) }
const root = ref<HTMLElement | null>(null)
onMounted(() => { for (const name of HELD) root.value?.addEventListener(name, hold, { capture: true }) })
onBeforeUnmount(() => { for (const name of HELD) root.value?.removeEventListener(name, hold, { capture: true }) })
const passing = computed(() => output.syncing.value || (series.byId(props.seriesId)?.statuses ?? []).includes("scanning"))
const canSync = computed(() => {
  const table = output.composition.value?.availability
  return table ? allowedIn(table, "sync", passing.value ? "scanning" : "idle") : !passing.value
})
const reads = useSeriesReadsStore()
const reading = computed(() => reads.forSeries(props.seriesId)
  .map((read) => ({ key: read.sourceId, text: t("output.reading", { site: pageHost(read.url), text: progressText(read) }) })))

type Filter = "all" | "deviations" | "errors" | "partial" | "working" | "off"
const FILTERS: Filter[] = ["all", "deviations", "errors", "partial", "working", "off"]
const filter = ref<Filter>("all")
const mode = ref<"cards" | "tiles" | "compilations">("cards")
const opened = ref<{ season: number; episode: number; compilation?: string } | null>(null)
const settingsOf = ref<{ season: number | null; compilation?: string } | null>(null)

const orderOf = (season: number): string[] =>
  (output.voiceOrders.value.find((each) => each.season === season)?.voices ?? []).map((voice) => voice.label)
const onlineOf = (season: number): Record<string, boolean> => Object.fromEntries(
  (output.voiceOrders.value.find((each) => each.season === season)?.voices ?? []).map((voice) => [voice.label, voice.online]))
const statusOf = (view: EpisodeView): EpisodeStatus => cutOverride(cutOf(cuts.value, view.season, view.episode))
  ?? episodeStatus(view, trackCells(view, orderOf(view.season)))
const PASS: Record<Filter, (view: EpisodeView) => boolean> = {
  all: () => true,
  deviations: isDeviation,
  errors: (view) => statusOf(view) === "error",
  partial: (view) => statusOf(view) === "partial",
  working: (view) => ["queued", "down", "align", "pack"].includes(statusOf(view)),
  off: (view) => statusOf(view) === "off",
}
const all = computed(() => output.seasons.value.flatMap((season) => season.episodes))
const counts = computed(() => Object.fromEntries(FILTERS.map((each) => [each, all.value.filter(PASS[each]).length])) as Record<Filter, number>)
const seasons = computed(() => output.seasons.value
  .map((season) => ({
    ...season,
    shown: season.episodes.filter(PASS[filter.value]),
    voices: output.voiceOrders.value.find((each) => each.season === season.season)?.voices ?? [],
  }))
  .filter((season) => season.shown.length > 0))
const legend = computed(() => Object.entries(output.marks.value).map(([id, mark]) => ({ id: Number(id), mark }))
  .sort((a, b) => a.mark.n - b.mark.n))

function open(season: number, episode: number | null): void {
  opened.value = episode === null ? null : { season, episode }
  if (episode !== null) void output.loadQueues(season, episode)
}
// The episodes selected, as in a file manager (lib/selection.ts): a click, Shift+click, Ctrl+click on the cards or tiles of a
// season; Space switches what is selected (off when any of it is on), one command per season; Esc lets it go. A filter
// changes what a run takes, so a new filter starts from nothing selected.
const selection = ref<Selection>(NO_SELECTION)
watch(filter, () => { selection.value = NO_SELECTION })
const shownOf = (season: number): number[] => seasons.value.find((each) => each.season === season)?.shown.map((view) => view.episode) ?? []
function select(season: number, episode: number, pick: Pick): void {
  selection.value = picked(selection.value, season, episode, pick, shownOf(season))
}
const NONE: number[] = []
const selectedBySeason = computed(() => new Map(seasons.value.map((season) =>
  [season.season, selectedOf(selection.value, season.season, season.shown.map((view) => view.episode))] as const)))
async function switchSelected(focused: { season: number; episode: number } | null): Promise<void> {
  const keys = selection.value.keys.size ? selection.value.keys : new Set(focused ? [keyOf(focused.season, focused.episode)] : [])
  const chosen = all.value.filter((view) => keys.has(keyOf(view.season, view.episode)))
  if (chosen.length === 0 || waiting.value) return
  const off = switchesOff(chosen.map((view) => !isOff(view)))
  for (const season of [...new Set(chosen.map((view) => view.season))]) {
    await output.switchTo(season, chosen.filter((view) => view.season === season).map((view) => view.episode), off)
  }
}
// The keys act inside the grid of a season only: Space and Esc anywhere else (a field, a switch) keep their own meaning.
function onKey(event: KeyboardEvent): void {
  const target = event.target as HTMLElement | null
  if (!target?.closest(".season-grid") || target.closest("input, textarea, select, [contenteditable]")) return
  if (event.key === " ") {
    event.preventDefault()  // a press of Space on a card or a tile is not a click of it
    if (event.type === "keydown" && !event.repeat) {
      const cell = target.closest<HTMLElement>("[data-episode]")
      const season = Number(target.closest<HTMLElement>("[data-season]")?.dataset.season)
      void switchSelected(cell && Number.isFinite(season) ? { season, episode: Number(cell.dataset.episode) } : null)
    }
  } else if (event.key === "Escape" && event.type === "keydown" && selection.value.keys.size) {
    event.stopPropagation()  // Esc lets the selection go; it closes the window only when nothing is selected
    selection.value = NO_SELECTION
  }
}

function openSettings(season: number | null, compilation: string | null = null): void {
  settingsOf.value = { season, ...(compilation === null ? {} : { compilation }) }
  void output.loadQueues(season, null, compilation)
}

// Stage 12: the compilations of each season (their outline and letters on the cards, their blocks in the view
// «Компиляции»), the cuts of the episodes, and the episode opened in a block of a compilation.
const compilationsOf = (season: number): CompilationView[] => output.compilations.value.filter((view) => view.season === season)
const cuts = computed(() => cutsBySlot(output.cuts.value))
const compilationSeasons = computed(() => output.seasons.value
  .map((season) => ({ season: season.season, episodes: season.episodes, views: compilationsOf(season.season) }))
  .filter((season) => season.views.length > 0))
function openInCompilation(season: number, key: string, episode: number | null): void {
  opened.value = episode === null ? null : { season, episode, compilation: key }
  if (episode !== null) void output.loadQueues(season, episode)
}
const settingsView = computed(() => output.compilations.value.find((view) => view.ref.key === settingsOf.value?.compilation))
const settingsCompilation = computed(() => {
  const view = settingsView.value
  if (!view) return undefined
  const counts: Partial<Record<CutState, number>> = {}
  const episodes = new Map((output.seasons.value.find((each) => each.season === view.season)?.episodes ?? [])
    .map((episode) => [episode.episode, episode] as const))
  const height = (_season: number, episode: number) => {
    const found = episodes.get(episode)
    return found && isOff(found) ? "off" as const : found?.references.find((each) => each.source_id != null)?.height ?? null
  }
  for (const each of view.episodes ?? []) {
    if (!takes(view, each.episode, height)) continue
    const state = cardStatus(true, cutOf(cuts.value, view.season, each.episode), heldCut(episodes.get(each.episode), view.ref.key)) as CutState
    counts[state] = (counts[state] ?? 0) + 1
  }
  const badge = badgeOf(view)
  return { title: t("output.settings.titleCompilation", { letter: badge.letter, range: `${view.ref.start}–${view.ref.end}` }),
    cuts: counts }
})

const scopeOf = (season: number | null, episode: number | null = null, compilation: string | null = null): Scope =>
  ({ ...(season === null ? {} : { season }), ...(episode === null ? {} : { episode }),
    ...(compilation === null ? {} : { compilation }) })
const queueOrder = (scope: Scope, queue: QueueName, group: string | null, rows: QueueRow[]) => output.setOrder(scope, queue, group, rows)
const queueAllow = (scope: Scope, queue: QueueName, row: QueueRow, allowed: boolean) => output.setAllowed(scope, queue, row, allowed)
const queueTake = (scope: Scope, group: string, taken: boolean) => output.setTaken(scope, group, taken)
const queueReset = (scope: Scope, queue: QueueName, group: string | null) => output.resetOrder(scope, queue, group)
const settingsSeason = computed(() => settingsOf.value?.season ?? null)
const settingsKey = computed(() => settingsOf.value?.compilation ?? null)
const settingsEpisodes = computed(() => settingsSeason.value === null ? []
  : output.seasons.value.find((each) => each.season === settingsSeason.value)?.episodes ?? [])
</script>

<template>
  <div ref="root" class="status-output" :class="{ 'is-waiting': waiting }" @keydown="onKey" @keyup="onKey">
    <div class="output-bar output-actions">
      <Button :label="t('output.settings.series')" icon="pi pi-sliders-h" size="small" severity="info" class="output-series-settings"
              @click="openSettings(null)" />
      <Button class="output-retry-failed" :label="t('output.retryFailed')" icon="pi pi-replay" size="small" severity="secondary"
              :disabled="!canSync" @click="output.retryFailed()" />
      <Button :label="t('output.sync')" :icon="passing ? 'pi pi-spin pi-spinner' : 'pi pi-refresh'" size="small" severity="warn"
              class="output-sync" :disabled="!canSync" @click="output.sync()" />
      <Button v-if="busy || passing" :label="t('sync.stop')" icon="pi pi-stop-circle" size="small" severity="danger"
              class="output-stop" :disabled="output.stopping.value" @click="output.stop()" />
    </div>
    <div v-if="legend.length" class="output-bar output-legend">
      <span class="output-legend-name">{{ t("output.sources") }}</span>
      <span v-for="each in legend" :key="each.id" class="output-source"><SourceMark :mark="each.mark" />{{ each.mark.place }}</span>
    </div>
    <div class="output-bar">
      <div class="output-filters" role="group">
        <button
          v-for="each in FILTERS" :key="each" type="button" class="output-filter" :class="[`is-${each}`, { 'is-on': filter === each }]"
          :disabled="each !== 'all' && counts[each] === 0" @click="filter = each"
        >{{ t(`output.filter.${each}`) }} <strong>{{ counts[each] }}</strong></button>
      </div>
      <span class="output-grow" />
      <div class="st-tabs output-view" role="group" :aria-label="t('output.view.label')">
        <button type="button" class="st-tab" :class="{ active: mode === 'cards' }" @click="mode = 'cards'">{{ t("output.view.cards") }}</button>
        <button type="button" class="st-tab" :class="{ active: mode === 'tiles' }" @click="mode = 'tiles'">{{ t("output.view.tiles") }}</button>
        <button type="button" class="st-tab output-view-compilations" :class="{ active: mode === 'compilations' }"
                @click="mode = 'compilations'">{{ t("output.view.compilations") }}</button>
      </div>
    </div>

    <PlanPending :series-id="props.seriesId" />
    <p v-for="read in reading" :key="read.key" class="output-reading"><i class="pi pi-spin pi-spinner" /><span>{{ read.text }}</span></p>
    <p v-if="output.failure.value" class="output-failure" role="alert">{{ output.failure.value }}</p>
    <p v-if="output.layoutFailure.value" class="output-failure output-layout-failure" role="alert">{{ output.layoutFailure.value }}</p>

    <div v-if="output.loading.value && all.length === 0" class="empty-state">{{ t("common.fetching") }}</div>
    <div v-else-if="all.length === 0" class="empty-state">{{ t("output.empty") }}</div>
    <template v-if="mode === 'compilations'">
      <div v-if="compilationSeasons.length === 0" class="empty-state">{{ t("output.compilations.empty") }}</div>
      <template v-for="season in compilationSeasons" :key="season.season">
        <CompilationBlock
          v-for="view in season.views" :key="view.ref.key" :view="view" :season="season.views" :episodes="season.episodes"
          :cuts="cuts" :marks="output.marks.value" :labels="output.labels.value" :order="orderOf(season.season)" mode="cards"
          :opened="opened?.compilation === view.ref.key ? opened.episode : null"
          :queues="opened?.compilation === view.ref.key ? output.queuesOf(season.season, opened.episode) : undefined" :busy="busy"
          @open="openInCompilation(season.season, view.ref.key, $event)" @settings="openSettings(season.season, view.ref.key)"
          @switch="output.takeCompilation(season.season, view.ref.key, !$event)"
          @member="(episode, key) => output.member(season.season, episode, key)"
          @retry="(episode) => output.retryCut(season.season, episode)"
          @queue-order="(episode, queue, group, rows) => queueOrder(scopeOf(season.season, episode), queue, group, rows)"
          @queue-allow="(episode, queue, row, allowed) => queueAllow(scopeOf(season.season, episode), queue, row, allowed)"
          @queue-reset="(episode, queue, group) => queueReset(scopeOf(season.season, episode), queue, group)"
        />
      </template>
    </template>
    <SeasonBlock
      v-for="season in mode === 'compilations' ? [] : seasons" :key="season.season" :season="season.season" :episodes="season.shown" :total="season.episodes.length"
      :voices="season.voices" :marks="output.marks.value" :names="output.names.value"
      :mode="mode === 'tiles' ? 'tiles' : 'cards'" :opened="opened?.season === season.season && !opened.compilation ? opened.episode : null"
      :compilations="compilationsOf(season.season)" :cuts="cuts"
      :queues="opened?.season === season.season ? output.queuesOf(season.season, opened.episode) : undefined" :busy="busy"
      :selected="selectedBySeason.get(season.season) ?? NONE"
      @open="open(season.season, $event)" @select="(episode, pick) => select(season.season, episode, pick)"
      @settings="openSettings(season.season)"
      @switch-season="output.switchTo(season.season, null, $event)"
      @switch-episode="(episode, off) => output.switchTo(season.season, [episode], off)"
      @voice-order="output.order(season.season, $event)"
      @voice-title="(label, title) => output.rename(label, title)"
      @refetch="(episode, part) => output.refetch(season.season, episode, part)"
      @retry="(episode, group) => output.retry(season.season, episode, group)"
      @redo="(episode, group) => output.redo(season.season, episode, group)"
      @queue-order="(episode, queue, group, rows) => queueOrder(scopeOf(season.season, episode), queue, group, rows)"
      @queue-allow="(episode, queue, row, allowed) => queueAllow(scopeOf(season.season, episode), queue, row, allowed)"
      @queue-take="(episode, group, taken) => queueTake(scopeOf(season.season, episode), group, taken)"
      @queue-reset="(episode, queue, group) => queueReset(scopeOf(season.season, episode), queue, group)"
    />

    <LevelSettings
      v-if="settingsOf" :key="`${settingsSeason}:${settingsKey}`" :season="settingsSeason" :episodes="settingsEpisodes"
      :order="settingsSeason === null ? [] : orderOf(settingsSeason)"
      :online="settingsSeason === null ? {} : onlineOf(settingsSeason)" :marks="output.marks.value"
      :queues="output.queuesOf(settingsSeason, null, settingsKey)" :busy="busy" :compilation="settingsCompilation"
      @close="settingsOf = null"
      @order="(queue, group, rows) => queueOrder(scopeOf(settingsSeason, null, settingsKey), queue, group, rows)"
      @allow="(queue, row, allowed) => queueAllow(scopeOf(settingsSeason, null, settingsKey), queue, row, allowed)"
      @take="(group, taken) => queueTake(scopeOf(settingsSeason, null, settingsKey), group, taken)"
      @reset="(queue, group) => queueReset(scopeOf(settingsSeason, null, settingsKey), queue, group)"
      @reset-level="output.reset(scopeOf(settingsSeason, null, settingsKey))"
      @retry-cuts="settingsSeason !== null && output.retryCut(settingsSeason, null)"
      @retry="(group) => settingsSeason !== null && output.retry(settingsSeason, null, group)"
      @redo="(group) => settingsSeason !== null && output.redo(settingsSeason, null, group)"
    />
  </div>
</template>

<style scoped>
.status-output { display: flex; flex-direction: column; gap: 0.9rem; }
.status-output.is-waiting :deep([data-lever]), .status-output.is-waiting :deep([data-lever] *) { cursor: progress; }
.output-bar { display: flex; flex-wrap: wrap; align-items: center; gap: 0.5rem 0.75rem; }
.output-grow { flex: 1 1 auto; }
.output-filters { display: flex; flex-wrap: wrap; gap: 0.3rem; }
.output-filter {
  display: inline-flex; align-items: center; gap: 0.4rem; padding: 0.25rem 0.7rem; border-radius: 20px; cursor: pointer;
  border: 1px solid rgba(0, 0, 0, 0.1); background: var(--bg-white); color: var(--color-gray-600); font: inherit; font-size: 0.8rem;
}
.output-filter.is-on { background: #2c3e50; border-color: #2c3e50; color: #fff; }
.output-filter:disabled { opacity: 0.45; cursor: default; }
.output-view .st-tab { padding-block: 0.3rem; font-size: 0.85rem; }
.output-legend { gap: 0.4rem; }
.output-legend-name { color: var(--text-muted); font-size: 0.85rem; }
.output-source {
  display: inline-flex; align-items: center; gap: 0.5rem; padding: 0.15rem 0.75rem 0.15rem 0.25rem; border-radius: 20px;
  background: var(--bg-light); border: 1px solid var(--border-color); font-size: 0.85rem; color: var(--color-gray-600);
}
.output-reading { margin: 0; display: flex; gap: 0.5rem; align-items: center; color: var(--text-muted); overflow-wrap: anywhere; }
.output-failure { margin: 0; color: var(--color-red-1); overflow-wrap: anywhere; }
</style>
