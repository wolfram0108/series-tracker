<script setup lang="ts">
import { computed, ref } from "vue"
import { t } from "../../../i18n"
import type { Queues } from "../../../composables/outputCalls"
import type { EpisodeStatus, EpisodeView, QueueName, QueueRow, SourceMark as Mark, TrackCell } from "../../../lib/output"
import PowerSwitch from "./PowerSwitch.vue"
import QueuePanel from "./QueuePanel.vue"
import TrackTable from "./TrackTable.vue"

// An episode opened below its row: the tracks with the actions that mend them, and the queues — of the reference and of
// each voice-over — whose order the user changes for this episode only.
const props = withDefaults(defineProps<{
  view: EpisodeView
  cells: TrackCell[]
  status: EpisodeStatus
  marks: Record<number, Mark>
  file?: string
  queues?: Queues
  busy?: boolean
}>(), { busy: false })

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
}>()

const tab = ref<string>("tracks")
const groups = computed(() => props.cells.map((cell) => cell.group))
const referenceGroup = computed(() => props.cells.find((cell) => cell.isReference)?.group ?? null)
const groupQueue = (group: string) => (props.queues?.groups ?? []).find((each) => each.group === group)
const PILL: Record<EpisodeStatus, string> = {
  ready: "pill-success", partial: "pill-warning", down: "pill-primary", align: "pill-primary", wait: "pill-secondary",
  error: "pill-danger", off: "pill-secondary",
}
</script>

<template>
  <section class="episode-detail" :data-episode="view.episode">
    <header class="detail-head">
      <h3 class="detail-title">{{ t("output.episode", { n: view.episode }) }}</h3>
      <span class="pill" :class="PILL[status]">{{ t(`output.status.${status}`) }}</span>
      <span v-if="file" class="detail-file">{{ file }}</span>
      <span class="detail-grow" />
      <button type="button" class="icon-btn" :aria-label="t('output.episode.prev')" @click="emit('step', -1)"><i class="pi pi-chevron-left" /></button>
      <button type="button" class="icon-btn" :aria-label="t('output.episode.next')" @click="emit('step', 1)"><i class="pi pi-chevron-right" /></button>
      <PowerSwitch
        :on="status !== 'off'" :busy="busy" :label="t(status === 'off' ? 'output.episode.enable' : 'output.episode.disable')"
        @toggle="emit('switch', !$event)"
      />
      <button type="button" class="icon-btn" :aria-label="t('output.episode.close')" @click="emit('close')"><i class="pi pi-times" /></button>
    </header>
    <div class="detail-body">
      <div class="st-tabs detail-tabs" role="tablist">
        <button type="button" class="st-tab" :class="{ active: tab === 'tracks' }" role="tab" :aria-selected="tab === 'tracks'" @click="tab = 'tracks'">
          {{ t("output.tab.tracks") }}
        </button>
        <button type="button" class="st-tab" :class="{ active: tab === 'reference' }" role="tab" :aria-selected="tab === 'reference'" @click="tab = 'reference'">
          {{ t("output.tab.reference") }}
        </button>
        <button
          v-for="(group, index) in groups" :key="group" type="button" class="st-tab" :class="{ active: tab === group }" role="tab"
          :aria-selected="tab === group" @click="tab = group"
        ><span class="figure-mark">{{ index + 1 }}</span>{{ group }}</button>
      </div>

      <TrackTable
        v-if="tab === 'tracks'" :view="view" :cells="cells" :marks="marks" :busy="busy"
        @refetch="emit('refetch', $event)" @retry="emit('retry', $event)" @redo="emit('redo', $event)"
      />
      <QueuePanel
        v-else-if="tab === 'reference'" queue="reference" level="episode" :view="queues?.reference" :marks="marks" :busy="busy"
        @order="emit('order', 'reference', null, $event)" @allow="(row, allowed) => emit('allow', 'reference', row, allowed)"
        @reset="emit('reset', 'reference', null)"
      />
      <QueuePanel
        v-else queue="voice" level="episode" :view="groupQueue(tab)" :marks="marks" :busy="busy" :from-reference="tab === referenceGroup"
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
  display: flex; flex-wrap: wrap; align-items: center; gap: 0.5rem 0.75rem; padding: 0.6rem 1rem;
  background: linear-gradient(135deg, var(--bg-light) 0%, var(--border-color) 100%); border-bottom: 1px solid var(--border-color);
}
.detail-title { margin: 0; font-size: 1rem; font-weight: 600; color: #2c3e50; }
.detail-file { font-family: var(--font-mono); font-size: 0.75rem; color: var(--text-muted); overflow-wrap: anywhere; }
.detail-grow { flex: 1 1 auto; }
.icon-btn {
  border: 1px solid var(--border-color); background: var(--bg-white); border-radius: 8px; cursor: pointer;
  color: var(--color-gray-600); font: inherit; padding: 0.3rem 0.6rem;
}
.detail-body { display: flex; flex-direction: column; gap: 0.75rem; padding: 0.75rem 1rem; }
.detail-tabs { flex-wrap: wrap; align-self: flex-start; }
</style>
