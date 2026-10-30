<script setup lang="ts">
import { computed, ref } from "vue"
import { api } from "../../api/client"
import { useApi } from "../../composables/useApi"
import { t } from "../../i18n"
import { dubRows, footText, isAccepted, isActive, resultMetrics, splitTitle, type DubRow, type DubState } from "../../lib/conform"
import { reasonText } from "../../lib/reason"
import { useQueuesStore, type ConformTask } from "../../stores/queues"
import { useVoicesStore } from "../../stores/voices"
import ConformPlotViewer from "./ConformPlotViewer.vue"

// The queue of the alignment service as the service holds it: a card per job. The picture is pushed by the core;
// a stop or a deletion changes the service and comes back as the next picture.
const q = useQueuesStore()
const voices = useVoicesStore()
const { request } = useApi()

const KNOWN = ["queued", "running", "done", "failed", "cancelled", "paused"] as const
const statusText = (status: string) =>
  (KNOWN as readonly string[]).includes(status) ? t(`agents.conform.status.${status as (typeof KNOWN)[number]}`) : status
const statusClass = (status: string) =>
  ({ done: "pill-success", failed: "pill-danger", running: "pill-primary", cancelled: "pill-secondary", paused: "pill-secondary" })[status] ?? "pill-info"

const dubStateText = (state: DubState): string =>
  state === "running" ? t("agents.conform.dub.running")
    : state === "cancelled" ? t("agents.conform.dub.cancelled")
      : t("agents.conform.dub.waiting")

const cards = computed(() =>
  (q.conform?.tasks ?? []).map((task) => ({
    task,
    ...splitTitle(task.label),
    rows: dubRows(task, (file) => voices.dubName(task.id, file)),
    foot: footText(task),
  })),
)

const str = (value: unknown) => String(value ?? "")
const percent = (task: ConformTask) => Math.round(task.progress * 100)

const pending = ref(new Set<string>())
async function change(task: ConformTask) {
  pending.value = new Set(pending.value).add(task.id)
  try {
    await request(api.DELETE("/api/conform/jobs/{job_id}", { params: { path: { job_id: task.id } } }), {
      errorMessage: t("agents.conform.changeFailed"),
    })
  } finally {
    const next = new Set(pending.value)
    next.delete(task.id)
    pending.value = next
  }
}

const plot = ref<{ src: string; title: string } | null>(null)
function openPlot(task: ConformTask, episode: string, row: DubRow) {
  if (!row.result?.plot) return
  plot.value = {
    src: `/api/conform/jobs/${encodeURIComponent(task.id)}/plot/${encodeURIComponent(row.result.plot)}`,
    title: t("agents.conform.plot.title", { dub: row.name, episode }),
  }
}
</script>

<template>
  <div v-if="!q.conform || !q.conform.daemon.up" class="queue-empty">
    {{ q.conform && q.conform.daemon.error
      ? t("agents.conform.daemon.down", { error: reasonText(q.conform.daemon.error) })
      : t("agents.conform.daemon.idle") }}
  </div>
  <template v-else>
    <div class="queue-row queue-status">
      <div class="pill pill-success"><i class="pi pi-check-circle"></i> {{ t("agents.conform.daemon.up") }}</div>
      <div v-if="q.conform.daemon.version" class="pill">{{ t("agents.conform.daemon.version", { version: str(q.conform.daemon.version) }) }}</div>
      <div v-if="q.conform.daemon.gpu_name || q.conform.daemon.device" class="pill">{{ str(q.conform.daemon.gpu_name || q.conform.daemon.device) }}</div>
    </div>
    <div v-if="!cards.length" class="queue-empty">{{ t("agents.conform.empty") }}</div>
    <div v-else class="composition-cards-container">
      <div v-for="card in cards" :key="card.task.id" class="card-final card-queue">
        <div class="queue-row head">
          <span class="queue-title">{{ card.title }}<span v-if="card.episode" class="episode">{{ card.episode }}</span></span>
          <div class="pill" :class="statusClass(card.task.status)">{{ statusText(card.task.status) }}</div>
          <button type="button" class="card-action" :disabled="pending.has(card.task.id)" @click="change(card.task)">
            <i class="pi" :class="isActive(card.task.status) ? 'pi-ban' : 'pi-trash'"></i>
            {{ isActive(card.task.status) ? t("agents.conform.action.cancel") : t("agents.conform.action.delete") }}
          </button>
        </div>
        <div v-if="card.task.status === 'running'" class="queue-row">
          <span class="stage">{{ card.task.stage }}</span>
          <div class="progress">
            <div class="progress-bar progress-bar-striped progress-bar-animated" :style="{ width: percent(card.task) + '%' }">{{ percent(card.task) }}%</div>
          </div>
        </div>
        <div v-if="card.rows.length" class="dubs">
          <div v-for="row in card.rows" :key="row.file" class="dub">
            <span class="dub-name">{{ row.name }}</span>
            <span class="dub-state" :class="{ dim: row.state !== 'result' }">
              <template v-if="row.result">{{ resultMetrics(row.result) }}</template>
              <template v-else>{{ dubStateText(row.state) }}</template>
            </span>
            <div v-if="row.result" class="pill" :class="isAccepted(row.result) ? 'pill-success' : 'pill-danger'">
              {{ isAccepted(row.result) ? t("agents.conform.verdict.accepted") : t("agents.conform.verdict.rejected") }}
            </div>
            <div v-else-if="row.state === 'running'" class="pill pill-primary">
              {{ t("agents.conform.dub.position", { index: card.task.dub_index, total: card.task.dub_total }) }}
            </div>
            <button v-if="row.result?.plot" type="button" class="plot-link" @click="openPlot(card.task, card.episode, row)">
              {{ t("agents.conform.plot.button") }}
            </button>
          </div>
        </div>
        <div class="foot"><span>{{ card.foot }}</span><span>{{ card.task.id }}</span></div>
      </div>
    </div>
    <ConformPlotViewer v-if="plot" :src="plot.src" :title="plot.title" @close="plot = null" />
  </template>
</template>

<style scoped>
.queue-status { flex-wrap: wrap; margin-bottom: 8px; }
.queue-status .pill { min-width: 0; max-width: 100%; overflow: hidden; text-overflow: ellipsis; }
.queue-empty {
  padding: 16px; text-align: center; color: var(--text-muted);
  background: var(--bg-light); border-radius: var(--border-radius);
}
.head { flex-wrap: wrap; }
.head .queue-title { flex: 1 1 auto; }
.card-action {
  font: inherit; font-size: 13px; font-weight: 500; height: 30px; padding: 0 12px; border-radius: 7px;
  border: 1px solid #ced4da; background: #fff; color: #343a40; cursor: pointer; flex-shrink: 0;
  display: inline-flex; align-items: center; gap: 6px; white-space: nowrap;
}
.card-action:hover:not(:disabled) { background: var(--bg-light); }
.card-action:disabled { opacity: 0.6; cursor: default; }
.episode { font-weight: 400; color: var(--text-muted); margin-left: 8px; }
.stage { font-size: 12px; color: var(--color-gray-600); white-space: nowrap; }
.dubs { background: rgba(255, 255, 255, 0.65); border: 1px solid #dee2e6; border-radius: 7px; }
/* A row of a voice-over breaks into lines when the window is narrow: the name keeps its own line before it is cut. */
.dub {
  display: flex; flex-wrap: wrap; gap: 6px 12px; align-items: center;
  padding: 7px 10px; border-bottom: 1px solid var(--border-color);
}
.dub:last-child { border-bottom: none; }
.dub-name { flex: 1 1 10rem; font-weight: 500; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dub-state { font-family: var(--font-mono); font-size: 12px; color: var(--color-gray-600); text-align: right; white-space: nowrap; }
.dub-state.dim { color: var(--text-muted); }
.plot-link {
  font: inherit; font-size: 12px; font-weight: 500; color: #0a58ca; background: #fff; cursor: pointer;
  border: 1px solid #b6d4fe; border-radius: 6px; padding: 2px 8px;
}
.plot-link:hover { background: #e7f1ff; }
.foot { display: flex; justify-content: space-between; gap: 8px; font-size: 12px; color: var(--text-muted); }
</style>
