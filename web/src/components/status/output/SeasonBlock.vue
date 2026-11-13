<script setup lang="ts">
import { computed } from "vue"
import type { ShownVoice } from "../../../lib/plan"
import type { Queues } from "../../../composables/outputCalls"
import { t } from "../../../i18n"
import {
  allOff, episodeStatus, fileNamesOf, isOff, spansOf, trackCells, type EpisodeView, type QueueName, type QueueRow,
  type SourceMark as Mark,
} from "../../../lib/output"
import { useCardGrid } from "../../../composables/useCardGrid"
import { useShownCells } from "../../../composables/useShownCells"
import { sharing } from "../../../lib/sharing"
import VoiceOrder from "../VoiceOrder.vue"
import { badgeOf, cutOf, cutOverride, membersOf, memberOf, takes, type CompilationView, type CutItem } from "../../../lib/compilations"
import { pickOf, type Pick } from "../../../lib/selection"
import { EPISODE_STATES, stateWord, TRACK_STATES, type StateName } from "../../../lib/states"
import GroupOutline from "./GroupOutline.vue"
import EpisodeCard from "./EpisodeCard.vue"
import EpisodeDetail from "./EpisodeDetail.vue"
import PowerSwitch from "./PowerSwitch.vue"

// A season: its header (the state of its videos and voice-overs, the order of the tracks of its containers, its settings)
// and its episodes as cards or tiles. How many cards a row holds follows the tracks and the number of episodes of the
// season and the width the grid has now (lib/output.ts: perRowOf); an opened episode unfolds below its row. A card is
// built when it comes near the view (composables/useShownCells.ts): a season of hundreds of episodes builds its rows on
// screen, the others are shells of the size of a card until then.
const props = withDefaults(defineProps<{
  season: number
  /** The episodes shown (a filter may hide some); `total` is how many the season has. */
  episodes: EpisodeView[]
  total: number
  voices: ShownVoice[]
  marks: Record<number, Mark>
  names?: { season?: number | null; episode?: number | null; part?: number | null; name?: string | null }[]
  mode?: "cards" | "tiles"
  opened?: number | null
  queues?: Queues
  busy?: boolean
  /** The compilations of the season (stage 12): their outline behind the cards and their letters on them. */
  compilations?: CompilationView[]
  /** The cuts of the episodes out of their compilations: a cut under way is the state of its episode. */
  cuts?: Map<string, CutItem>
  /** The episodes of the season selected (lib/selection.ts): their outline behind the cards, over the compilations'. */
  selected?: number[]
}>(), { names: () => [], mode: "cards", opened: null, busy: false, compilations: () => [], cuts: () => new Map(),
  selected: () => [] })

const emit = defineEmits<{
  (e: "open", episode: number | null): void
  (e: "select", episode: number, pick: Pick): void
  (e: "settings"): void
  (e: "switch-season", off: boolean): void
  (e: "switch-episode", episode: number, off: boolean): void
  (e: "voice-order", labels: string[]): void
  (e: "voice-title", label: string, title: string): void
  (e: "refetch", episode: number, part: number | null): void
  (e: "retry", episode: number, group: string): void
  (e: "redo", episode: number, group: string): void
  (e: "queue-order", episode: number, queue: QueueName, group: string | null, rows: QueueRow[]): void
  (e: "queue-allow", episode: number, queue: QueueName, row: QueueRow, allowed: boolean): void
  (e: "queue-take", episode: number, group: string, taken: boolean): void
  (e: "queue-reset", episode: number, queue: QueueName, group: string | null): void
}>()

const order = computed(() => props.voices.map((voice) => voice.label))
// The card of an episode the news left as it was is given the same props and is not drawn again (lib/sharing.ts).
const items = sharing(() => props.episodes.map((view) => {
  const cells = trackCells(view, order.value)
  const status = cutOverride(cutOf(props.cuts, view.season, view.episode)) ?? episodeStatus(view, cells)
  const source = view.references.find((each) => each.source_id != null)?.source_id
  const mark = source == null ? undefined : props.marks[source]
  return {
    view, cells, status, file: fileNamesOf(props.names, view.season, view.episode)[0],
    label: t("output.episode.card", { n: view.episode, status: stateWord(EPISODE_STATES[status]) }),
    tone: { "--source": mark ? `var(--source-${mark.color % 16})` : "transparent" },
  }
}))
const range = computed(() => spansOf(props.episodes.map((view) => view.episode)))
const off = computed(() => allOff(props.episodes))

// The state of the videos (the files of the references) and of the voice-overs of the season, counted as the cards are.
const meters = computed(() => {
  const video = { loaded: 0, pending: 0, error: 0 }
  const voices = { done: 0, down: 0, align: 0, queued: 0, wait: 0, absent: 0, off: 0, error: 0 }
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
const counted = (state: StateName, n: number): string => t("state.count", { state: stateWord(state), n })
const videoText = computed(() => [
  counted("done", meters.value.video.loaded),
  ...(meters.value.video.pending ? [counted("planned", meters.value.video.pending)] : []),
  ...(meters.value.video.error ? [counted("failed", meters.value.video.error)] : []),
].join(" · "))
const voicesText = computed(() => (Object.entries(meters.value.voices) as [keyof typeof meters.value.voices, number][])
  .filter(([, count]) => count > 0).map(([state, count]) => counted(TRACK_STATES[state], count)).join(" · "))

const { grid, ruler, wide, columns, layout } = useCardGrid(() => props.mode, () => order.value.length, () => props.total)
const { built, shell } = useShownCells(grid)

// The height of the reference each episode has now: a compilation no higher than it is not cut, whatever the layout says.
const heights = computed(() => new Map(props.episodes.map((view) => [view.episode,
  isOff(view) ? "off" as const : view.references.find((each) => each.source_id != null)?.height ?? null] as const)))
const height = (_season: number, episode: number) => heights.value.get(episode) ?? null
const members = computed(() => membersOf(props.compilations, height))
const outline = computed(() => props.compilations.map((view) => ({
  tone: `var(--compilation-${badgeOf(view).color})`,
  episodes: (view.episodes ?? []).filter((each) => takes(view, each.episode, height)).map((each) => each.episode),
})).filter((each) => each.episodes.length > 0))

const selection = computed(() => props.selected.length ? [{ episodes: props.selected, tone: "var(--selection-tone)" }] : [])
const chosen = computed(() => new Set(props.selected))
const toggleOpen = (episode: number): void => emit("open", opened.value?.view.episode === episode ? null : episode)
// A tile is selected by a click and opened by a double click, as a card; a press of Enter comes with no count of presses.
function clickedTile(episode: number, event: MouseEvent): void {
  if (event.detail === 0) toggleOpen(episode)
  else if (event.detail === 1) emit("select", episode, pickOf(event))
}

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
            <span data-state="done" :style="{ inlineSize: share(meters.video.loaded, videoAll) }" />
            <span data-state="failed" :style="{ inlineSize: share(meters.video.error, videoAll) }" />
          </span>
          <span class="meter-text">{{ videoText }}</span>
        </div>
        <div v-if="voicesAll" class="meter">
          <span class="meter-name">{{ t("output.meter.voices") }}</span>
          <span class="meter-bar">
            <span v-for="(count, state) in meters.voices" :key="state" :data-state="TRACK_STATES[state]" :style="{ inlineSize: share(count, voicesAll) }" />
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
      <VoiceOrder
        v-if="voices.length" :voices="voices" :busy="busy" @order="emit('voice-order', $event)"
        @title="(label, title) => emit('voice-title', label, title)"
      />
      <div ref="grid" class="season-grid" :class="`is-${mode}`" :style="columns">
        <span ref="ruler" class="grid-ruler" aria-hidden="true" />
        <GroupOutline :grid="grid" :layout="layout + episodes.length + (opened ? 1 : 0)" :groups="outline" />
        <GroupOutline :grid="grid" :layout="layout + episodes.length + (opened ? 1 : 0)" :groups="selection" />
        <template v-for="item in items" :key="item.view.episode">
          <EpisodeCard
            v-if="mode === 'cards' && (built(item.view.episode) || props.opened === item.view.episode)" :view="item.view" :cells="item.cells" :status="item.status" :marks="marks"
            :file="wide ? item.file : undefined" :open="opened?.view.episode === item.view.episode" :busy="busy"
            :badge="memberOf(members, season, item.view.episode)" selectable :selected="chosen.has(item.view.episode)"
            @open="toggleOpen(item.view.episode)" @select="emit('select', item.view.episode, $event)"
            @switch="emit('switch-episode', item.view.episode, $event)"
          />
          <div
            v-else-if="mode === 'cards'" :ref="shell(item.view.episode)" class="episode-card card-shell" :data-season="season"
            :data-episode="item.view.episode" aria-hidden="true"
          />
          <button
            v-else type="button" class="episode-tile" :data-episode="item.view.episode"
            :data-state="EPISODE_STATES[item.status]"
            :class="{ 'is-open': opened?.view.episode === item.view.episode, 'is-selected': chosen.has(item.view.episode) }"
            :style="item.tone" :title="item.label" :aria-label="item.label" :aria-pressed="chosen.has(item.view.episode)"
            @click="clickedTile(item.view.episode, $event)" @dblclick="toggleOpen(item.view.episode)"
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
