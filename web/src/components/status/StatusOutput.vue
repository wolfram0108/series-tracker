<script setup lang="ts">
import { computed, ref } from "vue"
import Button from "primevue/button"
import { useSeriesOutput } from "../../composables/useSeriesOutput"
import { t } from "../../i18n"
import {
  allowedIn, episodeStatus, isDeviation, trackCells, type EpisodeStatus, type EpisodeView, type QueueName, type QueueRow,
  type Scope,
} from "../../lib/output"
import { pageHost, progressText } from "../../lib/sources"
import { useSeriesStore } from "../../stores/series"
import { useSeriesReadsStore } from "../../stores/seriesReads"
import LevelSettings from "./output/LevelSettings.vue"
import SeasonBlock from "./output/SeasonBlock.vue"
import SourceMark from "./output/SourceMark.vue"

// The "Output" tab of a video series: its episodes season by season as cards (or tiles), each opened below its row with
// its tracks and its queues; the settings of a season and of the series are the same queues for all their episodes. The
// data is the core's: every change goes to it and comes back as events.
const props = defineProps<{ seriesId: number; seriesName: string }>()

const output = useSeriesOutput(props.seriesId, "video")
const series = useSeriesStore()
const seriesBusy = computed(() => !!series.byId(props.seriesId)?.is_busy)
const busy = computed(() => output.busy.value || seriesBusy.value)
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
const mode = ref<"cards" | "tiles">("cards")
const opened = ref<{ season: number; episode: number } | null>(null)
const settingsOf = ref<{ season: number | null } | null>(null)

const orderOf = (season: number): string[] =>
  (output.voiceOrders.value.find((each) => each.season === season)?.voices ?? []).map((voice) => voice.label)
const statusOf = (view: EpisodeView): EpisodeStatus => episodeStatus(view, trackCells(view, orderOf(view.season)))
const PASS: Record<Filter, (view: EpisodeView) => boolean> = {
  all: () => true,
  deviations: isDeviation,
  errors: (view) => statusOf(view) === "error",
  partial: (view) => statusOf(view) === "partial",
  working: (view) => ["down", "align"].includes(statusOf(view)),
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
function openSettings(season: number | null): void {
  settingsOf.value = { season }
  void output.loadQueues(season, null)
}

const scopeOf = (season: number | null, episode: number | null = null): Scope =>
  ({ ...(season === null ? {} : { season }), ...(episode === null ? {} : { episode }) })
const queueOrder = (scope: Scope, queue: QueueName, group: string | null, rows: QueueRow[]) => output.setOrder(scope, queue, group, rows)
const queueAllow = (scope: Scope, queue: QueueName, row: QueueRow, allowed: boolean) => output.setAllowed(scope, queue, row, allowed)
const queueTake = (scope: Scope, group: string, taken: boolean) => output.setTaken(scope, group, taken)
const queueReset = (scope: Scope, queue: QueueName, group: string | null) => output.resetOrder(scope, queue, group)
const settingsSeason = computed(() => settingsOf.value?.season ?? null)
const settingsEpisodes = computed(() => settingsSeason.value === null ? []
  : output.seasons.value.find((each) => each.season === settingsSeason.value)?.episodes ?? [])
</script>

<template>
  <div class="status-output">
    <div class="output-bar output-actions">
      <Button :label="t('output.settings.series')" icon="pi pi-sliders-h" size="small" severity="info" class="output-series-settings"
              @click="openSettings(null)" />
      <Button class="output-retry-failed" :label="t('output.retryFailed')" icon="pi pi-replay" size="small" severity="secondary"
              :disabled="!canSync" @click="output.retryFailed()" />
      <Button :label="t('output.sync')" :icon="passing ? 'pi pi-spin pi-spinner' : 'pi pi-refresh'" size="small" severity="warn"
              class="output-sync" :disabled="!canSync" @click="output.sync()" />
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
      </div>
    </div>

    <p v-for="read in reading" :key="read.key" class="output-reading"><i class="pi pi-spin pi-spinner" /><span>{{ read.text }}</span></p>
    <p v-if="output.failure.value" class="output-failure" role="alert">{{ output.failure.value }}</p>
    <p v-if="output.layoutFailure.value" class="output-failure output-layout-failure" role="alert">{{ output.layoutFailure.value }}</p>

    <div v-if="output.loading.value && all.length === 0" class="empty-state">{{ t("output.loading") }}</div>
    <div v-else-if="all.length === 0" class="empty-state">{{ t("output.empty") }}</div>
    <SeasonBlock
      v-for="season in seasons" :key="season.season" :season="season.season" :episodes="season.shown" :total="season.episodes.length"
      :voices="season.voices" :marks="output.marks.value" :names="output.names.value"
      :mode="mode" :opened="opened?.season === season.season ? opened.episode : null"
      :queues="opened?.season === season.season ? output.queuesOf(season.season, opened.episode) : undefined" :busy="busy"
      @open="open(season.season, $event)" @settings="openSettings(season.season)"
      @switch-season="output.switchTo(season.season, null, $event)"
      @switch-episode="(episode, off) => output.switchTo(season.season, episode, off)"
      @voice-order="output.order(season.season, $event)"
      @refetch="(episode, part) => output.refetch(season.season, episode, part)"
      @retry="(episode, group) => output.retry(season.season, episode, group)"
      @redo="(episode, group) => output.redo(season.season, episode, group)"
      @queue-order="(episode, queue, group, rows) => queueOrder(scopeOf(season.season, episode), queue, group, rows)"
      @queue-allow="(episode, queue, row, allowed) => queueAllow(scopeOf(season.season, episode), queue, row, allowed)"
      @queue-take="(episode, group, taken) => queueTake(scopeOf(season.season, episode), group, taken)"
      @queue-reset="(episode, queue, group) => queueReset(scopeOf(season.season, episode), queue, group)"
    />

    <LevelSettings
      v-if="settingsOf" :season="settingsSeason" :episodes="settingsEpisodes"
      :order="settingsSeason === null ? [] : orderOf(settingsSeason)" :marks="output.marks.value"
      :queues="output.queuesOf(settingsSeason, null)" :busy="busy"
      @close="settingsOf = null"
      @order="(queue, group, rows) => queueOrder(scopeOf(settingsSeason), queue, group, rows)"
      @allow="(queue, row, allowed) => queueAllow(scopeOf(settingsSeason), queue, row, allowed)"
      @take="(group, taken) => queueTake(scopeOf(settingsSeason), group, taken)"
      @reset="(queue, group) => queueReset(scopeOf(settingsSeason), queue, group)"
      @reset-level="output.reset(scopeOf(settingsSeason))"
      @retry="(group) => settingsSeason !== null && output.retry(settingsSeason, null, group)"
      @redo="(group) => settingsSeason !== null && output.redo(settingsSeason, null, group)"
    />
  </div>
</template>

<style scoped>
.status-output { display: flex; flex-direction: column; gap: 0.9rem; }
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
