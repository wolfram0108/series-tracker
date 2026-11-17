<script setup lang="ts">
import { computed, ref } from "vue"
import { t } from "../../../i18n"
import type { Queues } from "../../../composables/outputCalls"
import type { FailedAttempt, HeldFile, SubtitleLine, Taken } from "../../../lib/plan"
import type { SameGroups } from "../../../lib/same"
import type { EpisodeStatus, EpisodeView, QueueName, QueueRow, SourceMark as Mark, TrackCell } from "../../../lib/output"
import FoldButton from "../../FoldButton.vue"
import SyncButton from "../../SyncButton.vue"
import CompilationTable, { type CompilationRow } from "./CompilationTable.vue"
import PowerSwitch from "./PowerSwitch.vue"
import QueuePanel from "./QueuePanel.vue"
import TrackTable from "./TrackTable.vue"
import VoiceMark from "./VoiceMark.vue"
import { EPISODE_STATES, stateLabel } from "../../../lib/states"

// An episode opened below its row: the tracks with the actions that mend them, and the queues — of the reference and of
// each voice-over — whose order the user changes for this episode only.
const props = withDefaults(defineProps<{
  view: EpisodeView
  cells: TrackCell[]
  status: EpisodeStatus
  marks: Record<number, Mark>
  file?: string
  queues?: Queues
  /** What the file of the episode holds now: its queues show which of their variants it is. */
  held?: HeldFile | null
  /** The variant the plan takes for the reference of the episode: the queue of the reference marks it. */
  taken?: Taken | null
  /** The attempts of the plan that failed for the episode: the queues mark the rows they were of. */
  failed?: FailedAttempt[]
  busy?: boolean
  /** The view «Компиляции» (stage 12): the compilations that cover the episode, and its REF 2 instead of its tracks. */
  compilations?: CompilationRow[]
  /** The subtitles of the file of the episode (DESIGN.md 7.1.1). */
  subtitles?: SubtitleLine[]
}>(), { busy: false, compilations: undefined, taken: null, failed: () => [], subtitles: () => [] })

const emit = defineEmits<{
  (e: "close"): void
  (e: "step", by: number): void
  (e: "switch", off: boolean): void
  (e: "refetch", part: number | null): void
  (e: "retry", group: string): void
  (e: "redo", group: string): void
  (e: "order", queue: QueueName, group: string | null, rows: QueueRow[]): void
  (e: "allow", queue: QueueName, row: QueueRow, allowed: boolean): void
  (e: "take", group: string, taken: boolean): void
  (e: "reset", queue: QueueName, group: string | null): void
  (e: "same", queue: QueueName, groups: SameGroups | null): void
  (e: "member", key: string): void
  (e: "retry-cut"): void
  (e: "retry-failed"): void
}>()

const tab = ref<string>(props.compilations ? "compilations" : "tracks")
// The tab of the subtitles by a key no voice-over group can have: a group is a name a user typed.
const SUBTITLES = "\u0000subtitles"
const groups = computed(() => props.cells.map((cell) => cell.group))
const referenceGroup = computed(() => props.cells.find((cell) => cell.isReference)?.group ?? null)
const groupQueue = (group: string) => (props.queues?.groups ?? []).find((each) => each.group === group)
const failedOfGroup = (group: string) => props.failed.filter((each) => each.held.group === group)
</script>

<template>
  <section class="episode-detail fold-room" :data-episode="view.episode">
    <header class="detail-head fold-head">
      <div class="fold-said">
      <h3 class="detail-title">{{ t("output.episode", { n: view.episode }) }}</h3>
      <span class="pill" :data-state="EPISODE_STATES[status]">{{ stateLabel(EPISODE_STATES[status]) }}</span>
      <span v-if="file" class="detail-file">{{ file }}</span>
      </div>
      <div class="fold-does">
      <FoldButton :label="t('output.retryFailed')" :hint="t('output.episode.retryFailedHint')" icon="pi pi-undo" severity="secondary"
                  fold="lg" class="do-retry-failed" data-lever :disabled="busy" @click="emit('retry-failed')" />
      <SyncButton name="episode" :scope="{ season: view.season, episode: view.episode }" fold="lg" />
      <button type="button" class="icon-btn" :aria-label="t('output.episode.prev')" @click="emit('step', -1)"><i class="pi pi-chevron-left" /></button>
      <button type="button" class="icon-btn" :aria-label="t('output.episode.next')" @click="emit('step', 1)"><i class="pi pi-chevron-right" /></button>
      <PowerSwitch
        :on="status !== 'off'" :busy="busy" :label="t(status === 'off' ? 'output.episode.enable' : 'output.episode.disable')"
        @toggle="emit('switch', !$event)"
      />
      <button type="button" class="icon-btn" :aria-label="t('output.episode.close')" @click="emit('close')"><i class="pi pi-times" /></button>
      </div>
    </header>
    <div class="detail-body">
      <div v-if="compilations" class="st-tabs st-tabs-wrap" role="tablist">
        <button type="button" class="st-tab" :class="{ active: tab === 'compilations' }" role="tab"
                :aria-selected="tab === 'compilations'" @click="tab = 'compilations'">{{ t("output.tab.compilations") }}</button>
        <button type="button" class="st-tab" :class="{ active: tab === 'reference' }" role="tab"
                :aria-selected="tab === 'reference'" @click="tab = 'reference'">{{ t("output.tab.ref2") }}</button>
      </div>
      <div v-else class="st-tabs st-tabs-wrap" role="tablist">
        <button type="button" class="st-tab" :class="{ active: tab === 'tracks' }" role="tab" :aria-selected="tab === 'tracks'" @click="tab = 'tracks'">
          {{ t("output.tab.tracks") }}
        </button>
        <button type="button" class="st-tab" :class="{ active: tab === 'reference' }" role="tab" :aria-selected="tab === 'reference'" @click="tab = 'reference'">
          {{ t("output.tab.reference") }}
        </button>
        <button type="button" class="st-tab" :class="{ active: tab === 'markup' }" role="tab" :aria-selected="tab === 'markup'" @click="tab = 'markup'">
          {{ t("output.tab.markup") }}
        </button>
        <button
          v-for="(group, index) in groups" :key="group" type="button" class="st-tab" :class="{ active: tab === group }" role="tab"
          :aria-selected="tab === group" @click="tab = group"
        ><VoiceMark :n="index + 1" :online="cells[index]?.online" />{{ group }}</button>
        <button type="button" class="st-tab tab-subtitles" :class="{ active: tab === SUBTITLES }" role="tab"
                :aria-selected="tab === SUBTITLES" @click="tab = SUBTITLES">{{ t("output.tab.subtitles") }}</button>
      </div>

      <CompilationTable
        v-if="compilations && tab === 'compilations'" :rows="compilations" :marks="marks" :busy="busy" @member="emit('member', $event)"
        @retry="emit('retry-cut')"
      />
      <TrackTable
        v-else-if="tab === 'tracks'" :view="view" :cells="cells" :marks="marks" :busy="busy" :subtitles="subtitles"
        @refetch="emit('refetch', $event)" @retry="emit('retry', $event)" @redo="emit('redo', $event)"
        @drop-subtitles="emit('allow', 'subtitles', $event, false)"
      />
      <QueuePanel
        v-else-if="tab === SUBTITLES" queue="subtitles" level="episode" :view="queues?.subtitles" :marks="marks" :busy="busy"
        @order="emit('order', 'subtitles', null, $event)" @allow="(row, allowed) => emit('allow', 'subtitles', row, allowed)"
        @reset="emit('reset', 'subtitles', null)"
      />
      <QueuePanel
        v-else-if="tab === 'reference'" queue="reference" level="episode" :view="queues?.reference" :marks="marks" :held="held?.video" :taken="taken?.reference" :failed="failed" :busy="busy"
        :hint="compilations ? t('output.queue.hintRef2') : undefined"
        @order="emit('order', 'reference', null, $event)" @allow="(row, allowed) => emit('allow', 'reference', row, allowed)"
        @reset="emit('reset', 'reference', null)" @same="emit('same', 'reference', $event)"
      />
      <QueuePanel
        v-else-if="tab === 'markup'" queue="markup" level="episode" :view="queues?.markup" :follows="queues?.markup_follows"
        :marks="marks" :held="held?.timeline" :taken="queues?.markup_follows ? taken?.reference : taken?.markup" :failed="failed" :busy="busy"
        @order="emit('order', 'markup', null, $event)" @allow="(row, allowed) => emit('allow', 'markup', row, allowed)"
        @reset="emit('reset', 'markup', null)" @same="emit('same', 'markup', $event)"
      />
      <QueuePanel
        v-else queue="voice" level="episode" :view="groupQueue(tab)" :marks="marks" :held="held?.tracks[tab]" :failed="failedOfGroup(tab)" :busy="busy" :from-reference="tab === referenceGroup"
        @order="emit('order', 'voice', tab, $event)" @allow="(row, allowed) => emit('allow', 'voice', row, allowed)"
        @take="emit('take', tab, $event)" @reset="emit('reset', 'voice', tab)"
      />
    </div>
  </section>
</template>

<style scoped>
.episode-detail {
  grid-column: 1 / -1; background: var(--bg-white); border: 1px solid var(--border-color);
  border-radius: var(--card-border-radius); box-shadow: var(--card-hover-shadow); overflow: hidden;
}
.detail-head {
  padding: 0.6rem 1rem;
  background: linear-gradient(135deg, var(--bg-light) 0%, var(--border-color) 100%); border-bottom: 1px solid var(--border-color);
}
.detail-title { margin: 0; font-size: 1rem; font-weight: 600; color: #2c3e50; }
.detail-file { font-family: var(--font-mono); font-size: 0.75rem; color: var(--text-muted); overflow-wrap: anywhere; }
.icon-btn {
  border: 1px solid var(--border-color); background: var(--bg-white); border-radius: 8px; cursor: pointer;
  color: var(--color-gray-600); font: inherit; padding: 0.3rem 0.6rem;
}
.detail-body { display: flex; flex-direction: column; gap: 0.75rem; padding: 0.75rem 1rem; }
</style>
