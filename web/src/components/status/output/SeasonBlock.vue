<script setup lang="ts">
import { computed } from "vue"
import type { components } from "../../../api/schema"
import type { Queues } from "../../../composables/outputCalls"
import { t } from "../../../i18n"
import {
  allOff, episodeStatus, fileNamesOf, isOff, spansOf, trackCells, type EpisodeView, type QueueName, type QueueRow,
  type SourceMark as Mark,
} from "../../../lib/output"
import { useCardGrid } from "../../../composables/useCardGrid"
import VoiceOrder from "../VoiceOrder.vue"
import { badgeOf, membersOf, memberOf, takes, type CompilationView } from "../../../lib/compilations"
import CompilationOutline from "./CompilationOutline.vue"
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
  /** The compilations of the season (stage 12): their outline behind the cards and their letters on them. */
  compilations?: CompilationView[]
}>(), { names: () => [], mode: "cards", opened: null, busy: false, compilations: () => [] })

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

const { grid, ruler, wide, columns, layout } = useCardGrid(() => props.mode, () => order.value.length, () => props.total)

// The height of the reference each episode has now: a compilation no higher than it is not cut, whatever the layout says.
const heights = computed(() => new Map(props.episodes.map((view) => [view.episode,
  isOff(view) ? "off" as const : view.references.find((each) => each.source_id != null)?.height ?? null] as const)))
const height = (_season: number, episode: number) => heights.value.get(episode) ?? null
const members = computed(() => membersOf(props.compilations, height))
const outline = computed(() => props.compilations.map((view) => ({
  color: badgeOf(view).color,
  episodes: (view.episodes ?? []).filter((each) => takes(view, each.episode, height)).map((each) => each.episode),
})).filter((each) => each.episodes.length > 0))

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
        <CompilationOutline :grid="grid" :layout="layout + episodes.length + (opened ? 1 : 0)" :groups="outline" />
        <template v-for="item in items" :key="item.view.episode">
          <EpisodeCard
            v-if="mode === 'cards'" :view="item.view" :cells="item.cells" :status="item.status" :marks="marks"
            :file="wide ? item.file : undefined" :open="opened?.view.episode === item.view.episode" :busy="busy"
            :badge="memberOf(members, season, item.view.episode)"
            @open="emit('open', opened?.view.episode === item.view.episode ? null : item.view.episode)"
            @switch="emit('switch-episode', item.view.episode, $event)"
          />
          <button
            v-else type="button" class="episode-tile" :data-episode="item.view.episode"
            :class="[`is-${item.status}`, { 'is-open': opened?.view.episode === item.view.episode }]"
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
