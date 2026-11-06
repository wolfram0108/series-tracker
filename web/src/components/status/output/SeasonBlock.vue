<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue"
import type { components } from "../../../api/schema"
import type { Queues } from "../../../composables/outputCalls"
import { t } from "../../../i18n"
import {
  allOff, episodeStatus, fileNamesOf, perRowOf, spansOf, trackCells, type EpisodeView, type QueueName, type QueueRow,
  type SourceMark as Mark,
} from "../../../lib/output"
import VoiceOrder from "../VoiceOrder.vue"
import EpisodeCard from "./EpisodeCard.vue"
import EpisodeDetail from "./EpisodeDetail.vue"
import PowerSwitch from "./PowerSwitch.vue"

// A season: its header (the state of its videos and voice-overs, the order of the tracks of its containers, its settings)
// and its episodes as cards or tiles. How many cards a row holds follows the tracks and the number of episodes of the
// season and the width the grid has now (lib/output.ts: perRowOf); an opened episode unfolds below its row.
const props = withDefaults(defineProps<{
  season: number
  /** The episodes shown (a filter may hide some); `total` is how many the season has. */
  episodes: EpisodeView[]
  total: number
  voices: components["schemas"]["VoiceSpan"][]
  marks: Record<number, Mark>
  names?: { season?: number | null; episode?: number | null; part?: number | null; new_filename_preview?: string | null }[]
  mode?: "cards" | "tiles"
  opened?: number | null
  queues?: Queues
  busy?: boolean
}>(), { names: () => [], mode: "cards", opened: null, busy: false })

const emit = defineEmits<{
  (e: "open", episode: number | null): void
  (e: "settings"): void
  (e: "switch-season", off: boolean): void
  (e: "switch-episode", episode: number, off: boolean): void
  (e: "voice-order", labels: string[]): void
  (e: "refetch", episode: number, part: number | null): void
  (e: "retry", episode: number, group: string): void
  (e: "redo", episode: number, group: string): void
  (e: "queue-order", episode: number, queue: QueueName, group: string | null, rows: QueueRow[]): void
  (e: "queue-allow", episode: number, queue: QueueName, row: QueueRow, allowed: boolean): void
  (e: "queue-take", episode: number, group: string, taken: boolean): void
  (e: "queue-reset", episode: number, queue: QueueName, group: string | null): void
}>()

const order = computed(() => props.voices.map((voice) => voice.label))
const items = computed(() => props.episodes.map((view) => {
  const cells = trackCells(view, order.value)
  const status = episodeStatus(view, cells)
  const source = view.references.find((each) => each.source_id != null)?.source_id
  const mark = source == null ? undefined : props.marks[source]
  return {
    view, cells, status, file: fileNamesOf(props.names, view.season, view.episode)[0],
    label: t("output.episode.card", { n: view.episode, status: t(`output.status.${status}`) }),
    tone: { "--source": mark ? `var(--source-${mark.color % 16})` : "transparent" },
  }
}))
const range = computed(() => spansOf(props.episodes.map((view) => view.episode)))
const off = computed(() => allOff(props.episodes))

// The state of the videos (the files of the references) and of the voice-overs of the season, counted as the cards are.
const meters = computed(() => {
  const video = { loaded: 0, pending: 0, error: 0 }
  const voices = { done: 0, down: 0, align: 0, wait: 0, absent: 0, error: 0 }
  for (const item of items.value) {
    for (const cell of item.cells) {
      if (cell.isReference) {
        if (cell.state === "done") video.loaded += 1
        else if (cell.state === "error") video.error += 1
        else video.pending += 1
      } else if (cell.state !== "none") voices[cell.state] += 1
    }
  }
  return { video, voices }
})
const share = (count: number, all: number): string => `${all === 0 ? 0 : (count * 100) / all}%`
const voicesAll = computed(() => Object.values(meters.value.voices).reduce((sum, count) => sum + count, 0))
const videoAll = computed(() => meters.value.video.loaded + meters.value.video.pending + meters.value.video.error)
const videoText = computed(() => [
  t("output.meter.loaded", { n: meters.value.video.loaded }),
  ...(meters.value.video.pending ? [t("output.meter.pending", { n: meters.value.video.pending })] : []),
  ...(meters.value.video.error ? [t("output.meter.error", { n: meters.value.video.error })] : []),
].join(" · "))
const voicesText = computed(() => (Object.entries(meters.value.voices) as [keyof typeof meters.value.voices, number][])
  .filter(([, count]) => count > 0).map(([state, count]) => t(`output.meter.${state}`, { n: count })).join(" · "))

// How many of the smallest cards the grid has room for, measured on the grid itself.
const grid = ref<HTMLElement | null>(null)
const ruler = ref<HTMLElement | null>(null)
const places = ref(1)
let observer: ResizeObserver | null = null
function measure(): void {
  if (!grid.value || !ruler.value) return
  const unit = ruler.value.getBoundingClientRect().width
  const gap = parseFloat(getComputedStyle(grid.value).columnGap) || 0
  const width = grid.value.getBoundingClientRect().width
  places.value = unit > 0 ? Math.max(1, Math.floor((width + gap) / (unit + gap))) : 1
}
onMounted(() => {
  measure()
  if (typeof ResizeObserver !== "undefined" && grid.value) {
    observer = new ResizeObserver(measure)
    observer.observe(grid.value)
  }
})
onBeforeUnmount(() => observer?.disconnect())
watch(() => props.mode, measure, { flush: "post" })

const perRow = computed(() => perRowOf(places.value, order.value.length, props.total))
const wide = computed(() => perRow.value < Math.min(places.value, 6))
const columns = computed(() => props.mode === "cards" ? { gridTemplateColumns: `repeat(${perRow.value}, minmax(0, 1fr))` } : {})

const opened = computed(() => items.value.find((item) => item.view.episode === props.opened) ?? null)
function step(by: number): void {
  const at = items.value.findIndex((item) => item.view.episode === props.opened)
  const next = items.value[at + by]
  if (next) emit("open", next.view.episode)
}
</script>

<template>
  <section class="season-block modern-fieldset" :data-season="season">
    <header class="season-head">
      <span class="season-title">{{ t("output.season", { n: season }) }}</span>
      <span class="season-range">{{ t("output.season.range", { range, count: episodes.length }) }}</span>
      <div class="season-meters">
        <div class="meter">
          <span class="meter-name">{{ t("output.meter.video") }}</span>
          <span class="meter-bar">
            <span class="is-done" :style="{ inlineSize: share(meters.video.loaded, videoAll) }" />
            <span class="is-error" :style="{ inlineSize: share(meters.video.error, videoAll) }" />
          </span>
          <span class="meter-text">{{ videoText }}</span>
        </div>
        <div v-if="voicesAll" class="meter">
          <span class="meter-name">{{ t("output.meter.voices") }}</span>
          <span class="meter-bar">
            <span v-for="(count, state) in meters.voices" :key="state" :class="`is-${state}`" :style="{ inlineSize: share(count, voicesAll) }" />
          </span>
          <span class="meter-text">{{ voicesText }}</span>
        </div>
      </div>
      <button type="button" class="action-btn btn-status season-settings" @click="emit('settings')">
        <i class="pi pi-sliders-h" />{{ t("output.settings.season") }}
      </button>
      <PowerSwitch
        :on="!off" :busy="busy" :label="t(off ? 'output.season.enable' : 'output.season.disable')" @toggle="emit('switch-season', !$event)"
      />
    </header>
    <div class="season-body">
      <VoiceOrder v-if="voices.length > 1" :voices="voices" :busy="busy" @order="emit('voice-order', $event)" />
      <div ref="grid" class="season-grid" :class="`is-${mode}`" :style="columns">
        <span ref="ruler" class="grid-ruler" aria-hidden="true" />
        <template v-for="item in items" :key="item.view.episode">
          <EpisodeCard
            v-if="mode === 'cards'" :view="item.view" :cells="item.cells" :status="item.status" :marks="marks"
            :file="wide ? item.file : undefined" :open="opened?.view.episode === item.view.episode" :busy="busy"
            @open="emit('open', opened?.view.episode === item.view.episode ? null : item.view.episode)"
            @switch="emit('switch-episode', item.view.episode, $event)"
          />
          <button
            v-else type="button" class="episode-tile" :class="[`is-${item.status}`, { 'is-open': opened?.view.episode === item.view.episode }]"
            :style="item.tone" :title="item.label" :aria-label="item.label"
            @click="emit('open', opened?.view.episode === item.view.episode ? null : item.view.episode)"
          >{{ item.view.episode }}</button>
          <EpisodeDetail
            v-if="opened?.view.episode === item.view.episode" :view="item.view" :cells="item.cells" :status="item.status"
            :marks="marks" :file="item.file" :queues="queues" :busy="busy"
            @close="emit('open', null)" @step="step" @switch="emit('switch-episode', item.view.episode, $event)"
            @refetch="emit('refetch', item.view.episode, $event)" @retry="emit('retry', item.view.episode, $event)"
            @redo="emit('redo', item.view.episode, $event)"
            @order="(queue, group, rows) => emit('queue-order', item.view.episode, queue, group, rows)"
            @allow="(queue, row, allowed) => emit('queue-allow', item.view.episode, queue, row, allowed)"
            @take="(group, taken) => emit('queue-take', item.view.episode, group, taken)"
            @reset="(queue, group) => emit('queue-reset', item.view.episode, queue, group)"
          />
        </template>
      </div>
    </div>
  </section>
</template>

<style scoped>
.season-block { margin: 0; }
.season-head {
  display: flex; flex-wrap: wrap; align-items: center; gap: 0.5rem 1rem; padding: 0.75rem 1.25rem;
  background: linear-gradient(135deg, var(--bg-light) 0%, var(--border-color) 100%); border-bottom: 1px solid var(--border-color);
  border-radius: var(--card-border-radius) var(--card-border-radius) 0 0;
}
.season-title { font-size: 1rem; font-weight: 600; color: #2c3e50; }
.season-range { color: var(--text-muted); font-size: 0.8rem; }
.season-meters { display: flex; flex-wrap: wrap; gap: 0.25rem 1.1rem; flex: 1 1 var(--measure); }
.meter { display: flex; align-items: center; gap: 0.5rem; flex: 1 1 14rem; min-inline-size: 0; }
.meter-name { flex: none; inline-size: 3.6rem; color: var(--text-muted); font-size: 0.75rem; }
.meter-bar { display: flex; flex: 1 1 auto; block-size: 0.5rem; border-radius: 4px; overflow: hidden; background: #dee2e6; }
.meter-bar .is-done { background: #409444; }
.meter-bar .is-down { background: #009688; }
.meter-bar .is-align { background: var(--color-blue); }
.meter-bar .is-wait { background: #ced4da; }
.meter-bar .is-error { background: var(--color-red-1); }
.meter-bar .is-absent { background: var(--color-yellow-1); }
.meter-text { font-size: 0.75rem; color: var(--color-gray-600); white-space: nowrap; }
.season-settings { min-block-size: 2rem; padding-block: 0.25rem; font-size: 0.85rem; }
.season-body { display: flex; flex-direction: column; gap: 0.75rem; padding: 0.75rem 1.25rem; }
.season-grid { position: relative; display: grid; grid-auto-flow: row dense; gap: 0.6rem; }
.season-grid.is-tiles { grid-template-columns: repeat(auto-fill, minmax(2.25rem, 1fr)); gap: 0.25rem; }
/* The width of the smallest card: a strip of six tracks and the padding around it (TrackStrip, EpisodeCard). */
.grid-ruler { position: absolute; visibility: hidden; pointer-events: none; inline-size: calc(6 * 1.25rem + 5 * 0.125rem + 1.4rem); }
.episode-tile {
  all: unset; box-sizing: border-box; position: relative; overflow: hidden; block-size: 1.6rem; border-radius: calc(1.6rem * var(--round)); cursor: pointer;
  display: grid; place-items: center; font-size: 0.65rem; font-weight: 700; line-height: 1; font-variant-numeric: tabular-nums;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.12);
}
.episode-tile::after { content: ""; position: absolute; inset-inline: 0; inset-block-end: 0; block-size: 0.25rem; background: var(--source); }
.episode-tile:focus-visible, .episode-tile.is-open { outline: 0.125rem solid var(--color-blue); outline-offset: 0.0625rem; }
.episode-tile.is-ready { background: linear-gradient(135deg, #4CAF50, #409444); color: #fff; }
.episode-tile.is-down { background: linear-gradient(135deg, #009688, #00796D); color: #fff; }
.episode-tile.is-align { background: var(--color-blue); color: #fff; }
.episode-tile.is-wait { background: linear-gradient(135deg, #f0f2f5, #e9ecef); color: var(--color-gray-600); }
.episode-tile.is-partial {
  background: repeating-linear-gradient(45deg, #e9ecef 0 0.4rem, #8bc790 0.4rem 0.8rem); color: var(--color-gray-600);
}
.episode-tile.is-error { background: linear-gradient(135deg, #dc3545, #c82333); color: #fff; }
.episode-tile.is-off { background: linear-gradient(135deg, #6c757d, #5a6268); color: #fff; }
</style>
