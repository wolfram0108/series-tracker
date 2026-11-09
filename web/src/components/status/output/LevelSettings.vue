<script setup lang="ts">
import { computed, ref } from "vue"
import ModalShell from "../../ModalShell.vue"
import type { Queues } from "../../../composables/outputCalls"
import { t } from "../../../i18n"
import type { CutState } from "../../../lib/compilations"
import { trackCells, type EpisodeView, type QueueName, type QueueRow, type SourceMark as Mark, type TrackState } from "../../../lib/output"
import QueuePanel from "./QueuePanel.vue"
import VoiceMark from "./VoiceMark.vue"

// The settings of a level — a season, or the whole series — as one table per queue, the order set for all of its
// episodes at once; a season also has its tracks, with the actions that mend all the episodes of a group.
const props = withDefaults(defineProps<{
  season: number | null
  /** The episodes of the season (none for the series): what the tab of the tracks counts. */
  episodes?: EpisodeView[]
  order: string[]
  /** The presence of each voice-over of the order: a source gives it, or only the files hold it. */
  online?: Record<string, boolean>
  marks: Record<number, Mark>
  queues?: Queues
  busy?: boolean
  /** The level of a compilation of the season (stage 12): its title, and how many of its episodes are cut, by state. */
  compilation?: { title: string; cuts: Partial<Record<CutState, number>> }
}>(), { episodes: () => [], online: () => ({}), busy: false, compilation: undefined })

const emit = defineEmits<{
  (e: "close"): void
  (e: "order", queue: QueueName, group: string | null, rows: QueueRow[]): void
  (e: "allow", queue: QueueName, row: QueueRow, allowed: boolean): void
  (e: "take", group: string, taken: boolean): void
  (e: "reset", queue: QueueName, group: string | null): void
  (e: "reset-level"): void
  (e: "retry", group: string): void
  (e: "redo", group: string): void
  (e: "retry-cuts"): void
}>()

const level = computed(() => (props.compilation ? "compilation" : props.season === null ? "series" : "season"))
const tab = ref<string>(props.compilation ? "cuts" : props.season === null ? "reference" : "tracks")
// A compilation has its REF 2 and its cuts; the voice-overs are set on the season and on the episode.
const groups = computed(() => props.compilation ? []
  : props.queues ? (props.queues.groups ?? []).map((each) => each.group) : props.order)
const CUT_PILL: Record<CutState, string> = {
  wait: "pill-secondary", down: "pill-primary", align: "pill-primary", ready: "pill-success", error: "pill-danger",
}
const cutEntries = computed(() => Object.entries(props.compilation?.cuts ?? {}).filter(([, count]) => count) as [CutState, number][])
const groupQueue = (group: string) => (props.queues?.groups ?? []).find((each) => each.group === group)

// How many episodes each track of the season is in each state.
const tracks = computed(() => props.order.map((group, index) => {
  const counts: Partial<Record<TrackState | "reference", number>> = {}
  for (const view of props.episodes) {
    const cell = trackCells(view, [group])[0]
    if (cell.isReference) counts.reference = (counts.reference ?? 0) + 1
    else if (cell.state !== "none") counts[cell.state] = (counts[cell.state] ?? 0) + 1
  }
  return { group, pos: index + 1, counts }
}))
const PILL: Partial<Record<TrackState | "reference", string>> = {
  reference: "pill-primary", done: "pill-success", down: "pill-primary", align: "pill-primary", wait: "pill-secondary",
  absent: "pill-warning", off: "pill-secondary", error: "pill-danger",
}
const countText = (state: TrackState | "reference", count: number): string => state === "reference"
  ? `${t("output.queue.reference")} ${count}` : t(`output.meter.${state === "none" ? "wait" : state}`, { n: count })
</script>

<template>
  <ModalShell size="xl" fixed-height @close="emit('close')">
    <template #title>{{ compilation ? compilation.title : season === null ? t("output.settings.titleSeries")
      : t("output.settings.titleSeason", { n: season }) }}</template>
    <template #header-extra>
      <div class="header-tabs st-tabs" role="tablist">
        <button v-if="compilation" type="button" class="st-tab" :class="{ active: tab === 'cuts' }" @click="tab = 'cuts'">
          {{ t("output.tab.cuts") }}
        </button>
        <button v-else-if="season !== null" type="button" class="st-tab" :class="{ active: tab === 'tracks' }" @click="tab = 'tracks'">
          {{ t("output.tab.tracks") }}
        </button>
        <button type="button" class="st-tab" :class="{ active: tab === 'reference' }" @click="tab = 'reference'">
          {{ compilation ? t("output.tab.ref2") : t("output.tab.reference") }}
        </button>
        <button v-for="(group, index) in groups" :key="group" type="button" class="st-tab" :class="{ active: tab === group }" @click="tab = group">
          <VoiceMark :n="index + 1" :online="online[group]" />{{ group }}
        </button>
      </div>
    </template>

    <div class="level-settings">
      <div v-if="tab === 'cuts' && compilation" class="level-cuts">
        <span class="level-counts">
          <span v-for="[state, count] in cutEntries" :key="state" class="pill" :class="CUT_PILL[state]">
            {{ t(`output.cut.meter.${state}`, { n: count }) }}
          </span>
        </span>
        <button v-if="compilation.cuts.error" type="button" class="level-action do-retry-cuts" data-lever :disabled="busy" @click="emit('retry-cuts')">
          {{ t("output.cut.retryAll", { n: compilation.cuts.error }) }}
        </button>
      </div>
      <table v-else-if="tab === 'tracks'" class="level-tracks">
        <thead>
          <tr>
            <th class="narrow">{{ t("output.tracks.place") }}</th>
            <th>{{ t("output.tracks.track") }}</th>
            <th>{{ t("output.tracks.status") }}</th>
            <th class="narrow">{{ t("output.tracks.actions") }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="track in tracks" :key="track.group" :data-group="track.group">
            <td><VoiceMark :n="track.pos" :online="online[track.group]" /></td>
            <td><b>{{ track.group }}</b></td>
            <td class="level-counts">
              <span v-for="(count, state) in track.counts" :key="state" class="pill" :class="PILL[state]">{{ countText(state, count!) }}</span>
            </td>
            <td class="narrow">
              <button v-if="track.counts.error" type="button" class="level-action do-retry" data-lever :disabled="busy" @click="emit('retry', track.group)">
                {{ t("output.action.retryAll", { n: track.counts.error }) }}
              </button>
              <button v-if="track.counts.done" type="button" class="level-action do-redo" data-lever :disabled="busy" @click="emit('redo', track.group)">
                {{ t("output.action.redoAll") }}
              </button>
            </td>
          </tr>
        </tbody>
      </table>
      <QueuePanel
        v-else-if="tab === 'reference'" queue="reference" :level="level" :view="queues?.reference" :marks="marks" :busy="busy"
        :hint="compilation ? t('output.queue.hintRef2Level') : undefined"
        @order="emit('order', 'reference', null, $event)" @allow="(row, allowed) => emit('allow', 'reference', row, allowed)"
        @reset="emit('reset', 'reference', null)"
      />
      <QueuePanel
        v-else queue="voice" :level="level" :view="groupQueue(tab)" :marks="marks" :busy="busy"
        @order="emit('order', 'voice', tab, $event)" @allow="(row, allowed) => emit('allow', 'voice', row, allowed)"
        @take="emit('take', tab, $event)" @reset="emit('reset', 'voice', tab)"
      />
    </div>

    <template #footer>
      <button type="button" class="level-footer" data-lever :disabled="busy" @click="emit('reset-level')">{{ t("output.settings.reset") }}</button>
      <button type="button" class="level-footer" @click="emit('close')">{{ t("output.settings.close") }}</button>
    </template>
  </ModalShell>
</template>

<style scoped>
.level-settings { display: flex; flex-direction: column; gap: 0.75rem; }
.level-tracks { inline-size: 100%; border-collapse: collapse; border: 1px solid var(--border-color); font-size: 0.85rem; }
.level-tracks th {
  background: var(--bg-light); font-weight: 700; text-align: start; padding: 0.5rem 0.6rem; border-bottom: 1px solid var(--border-color);
}
.level-tracks td { padding: 0.4rem 0.6rem; border-bottom: 1px solid var(--border-color); vertical-align: middle; }
.narrow { inline-size: 1%; white-space: nowrap; }
.level-counts { display: flex; flex-wrap: wrap; gap: 0.3rem; }
.level-cuts { display: flex; flex-wrap: wrap; align-items: center; gap: 0.5rem 1rem; }
.level-action, .level-footer {
  border: 1px solid var(--border-color); background: var(--bg-white); border-radius: 8px; padding: 0.3rem 0.7rem;
  cursor: pointer; color: var(--color-gray-600); font: inherit; margin-inline-end: 0.3rem;
}
.level-action:disabled, .level-footer:disabled { opacity: 0.5; cursor: default; }
</style>
