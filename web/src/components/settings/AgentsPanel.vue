<script setup lang="ts">
import { computed, ref } from "vue"
import { api } from "../../api/client"
import { useApi } from "../../composables/useApi"
import { stopReading } from "../../composables/sourceCalls"
import { useSSE } from "../../composables/useSSE"
import { t } from "../../i18n"
import { reasonText } from "../../lib/reason"
import { stateLabel, type StateName } from "../../lib/states"
import {
  conformWork, downloadWork, journalWork, monitorWork, packWork, processingWork, readWork, slicingWork, type WorkAction, type WorkView,
} from "../../lib/work"
import { useQueuesStore } from "../../stores/queues"
import { useSeriesReadsStore } from "../../stores/seriesReads"
import { useSeriesStore } from "../../stores/series"
import { useVoicesStore } from "../../stores/voices"
import SheetField from "../SheetField.vue"
import SheetRow from "../SheetRow.vue"
import StSelect from "../StSelect.vue"
import StValue from "../StValue.vue"
import ConformPlotViewer from "./ConformPlotViewer.vue"
import WorkCard from "./WorkCard.vue"

// The agents: every work the core is doing, as cards of one kind (lib/work.ts), by the agent that does it. Every card is of a
// series, so the section is filtered by it: in the settings by the one the user picks (all by default), in the window of a
// series by that series. The queues are pictures the core pushes whole; a stop or a deletion changes the core and comes back
// as the next picture.
const props = defineProps<{ seriesId?: number | null }>()

const q = useQueuesStore()
const voices = useVoicesStore()
const reads = useSeriesReadsStore()
const seriesStore = useSeriesStore()
const { request } = useApi()
// The queues are pictures the stream brings: before it is up the window knows nothing of them, so it does not say they are empty.
const { connected: live } = useSSE()

const picked = ref<number | null>(null)
const filter = computed(() => props.seriesId ?? picked.value)

interface Section { key: string; icon: string; title: string; chip: string; tone: StateName; views: WorkView[] }
const sections = computed<Section[]>(() => [
  { key: "download", icon: "pi-download", title: t("download.title"), chip: stateLabel("download"), tone: "download",
    views: q.downloads.map(downloadWork) },
  { key: "conform", icon: "pi-sliders-h", title: t("agents.conform.title"), chip: stateLabel("align"), tone: "align",
    views: (q.conform?.tasks ?? []).map((task) => conformWork(task, (file) => voices.dubName(task.id, file))) },
  { key: "pack", icon: "pi-box", title: t("agents.pack.title"), chip: stateLabel("pack"), tone: "pack",
    views: [...q.journals.map(journalWork), ...q.pack.map(packWork)] },
  { key: "slicing", icon: "pi-clone", title: t("agents.slicing.title"), chip: stateLabel("slice"), tone: "slice", views: q.slicing.map(slicingWork) },
  { key: "processing", icon: "pi-cog", title: t("agents.processing.title"), chip: t("agents.chip.processing"), tone: "renaming",
    views: q.agent.map(processingWork) },
  { key: "monitor", icon: "pi-wifi", title: t("agents.torrents.title"), chip: t("agents.chip.torrents"), tone: "download",
    views: q.torrents.map(monitorWork) },
  { key: "read", icon: "pi-globe", title: t("agents.reads.title"), chip: stateLabel("read"), tone: "read", views: reads.all().map(readWork) },
])

// A work that does not tell its series (none yet: an alignment of a key of another maker) shows only where no series is picked.
const ofFilter = (view: WorkView): boolean => filter.value === null || view.seriesId === filter.value
const shown = computed(() => sections.value.map((section) => ({ ...section, shown: section.views.filter(ofFilter) })))

const nameOf = (view: WorkView): string => (view.seriesId == null ? "" : seriesStore.byId(view.seriesId)?.name ?? view.seriesName)

const seriesOptions = computed(() => {
  const ids = new Set<number>(sections.value.flatMap((section) => section.views.flatMap((view) => (view.seriesId == null ? [] : [view.seriesId]))))
  if (filter.value !== null) ids.add(filter.value)
  return [{ label: t("agents.filter.all"), value: null as number | null },
    ...[...ids].sort((a, b) => a - b).map((id) => ({ label: `#${id} ${seriesStore.byId(id)?.name ?? ""}`.trim(), value: id as number | null }))]
})
const lockedLabel = computed(() => seriesOptions.value.find((each) => each.value === props.seriesId)?.label ?? `#${props.seriesId}`)

const pending = ref(new Set<string>())
async function act(view: WorkView, name: WorkAction): Promise<void> {
  pending.value = new Set(pending.value).add(view.id)
  try {
    if (view.kind === "download") {
      await request(api.DELETE("/api/downloads/cards/{card_id}", { params: { path: { card_id: view.ref } } }))
    } else if (view.kind === "pack") {
      await request(api.DELETE("/api/pack/cards/{card_id}", { params: { path: { card_id: view.ref } } }))
    } else if (view.kind === "conform") {
      await request(api.DELETE("/api/conform/jobs/{job_id}", { params: { path: { job_id: view.ref } } }), {
        errorMessage: t("agents.conform.changeFailed"),
      })
    } else if (name === "stopRead") {
      await stopReading(view.ref)
    }
  } finally {
    const next = new Set(pending.value)
    next.delete(view.id)
    pending.value = next
  }
}

const plot = ref<{ src: string; title: string } | null>(null)
const str = (value: unknown) => String(value ?? "")
</script>

<template>
  <div class="agents-panel">
    <div class="agents-filter">
      <SheetField v-if="seriesId != null" class="agents-select is-readonly" :label="t('agents.filter.series')" icon="pi pi-filter">
        <StValue class="agents-fixed" :value="lockedLabel" />
      </SheetField>
      <SheetField v-else class="agents-select" :label="t('agents.filter.series')" icon="pi pi-filter">
        <StSelect v-model="picked" :options="seriesOptions" />
      </SheetField>
      <span class="agents-chips">
        <span v-for="section in shown" :key="section.key" class="pill agents-chip" :data-state="section.tone" :class="{ 'is-empty': !section.shown.length }">
          {{ section.chip }} <b>{{ section.shown.length }}</b>
        </span>
      </span>
    </div>

    <div class="sheet">
      <SheetRow v-for="section in shown" :key="section.key" class="agents-section" :data-section="section.key" :caption="section.title" :count="section.shown.length">
        <template v-if="section.key === 'conform'">
          <div v-if="!q.conform || !q.conform.daemon.up" class="queue-empty">
            {{ q.conform && q.conform.daemon.error
              ? t("agents.conform.daemon.down", { error: reasonText(q.conform.daemon.error) })
              : t("agents.conform.daemon.idle") }}
          </div>
          <div v-else class="agents-daemon">
            <span class="pill" data-state="done">{{ t("agents.conform.daemon.up") }}</span>
            <span v-if="q.conform.daemon.version" class="pill">{{ t("agents.conform.daemon.version", { version: str(q.conform.daemon.version) }) }}</span>
            <span v-if="q.conform.daemon.gpu_name || q.conform.daemon.device" class="pill">{{ str(q.conform.daemon.gpu_name || q.conform.daemon.device) }}</span>
          </div>
        </template>
        <div v-if="!section.shown.length" class="queue-empty">{{ live ? t("agents.empty") : t("common.fetching") }}</div>
        <div v-else class="agents-cards">
          <WorkCard
            v-for="view in section.shown" :key="view.id" :view="view" :series-name="nameOf(view)" :busy="pending.has(view.id)"
            @action="act(view, $event)" @plot="plot = $event"
          />
        </div>
      </SheetRow>
    </div>

    <ConformPlotViewer v-if="plot" :src="plot.src" :title="plot.title" @close="plot = null" />
  </div>
</template>

<style scoped>
.agents-panel { display: flex; flex-direction: column; gap: 20px; }
.agents-filter { display: flex; flex-wrap: wrap; align-items: center; gap: 10px 14px; }
.agents-select { inline-size: min(22rem, 100%); }
.agents-chips { display: flex; flex-wrap: wrap; gap: 6px; margin-inline-start: auto; }
/* An empty queue is a chip that is quiet: its tint and its edge are the faded ones, its words are not (4.5:1 on its tint). */
.agents-chip.is-empty {
  background-color: color-mix(in srgb, var(--tone) 12%, transparent);
  border-color: color-mix(in srgb, var(--tone-end) 30%, transparent);
  color: color-mix(in srgb, var(--tone-end) 72%, black);
}
.agents-cards { display: flex; flex-direction: column; gap: 8px; }
.agents-daemon { display: flex; flex-wrap: wrap; gap: 8px; }
.queue-empty { padding: 16px; text-align: center; color: var(--text-muted); background: var(--bg-light); border-radius: var(--border-radius); }
</style>
