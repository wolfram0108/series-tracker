<script setup lang="ts">
import { useQueuesStore, type ConformResult } from "../../stores/queues"
import { t } from "../../i18n"

// Вкладка «Агенты»: 4 очереди карточками (вместо таблиц). Данные — из
// queuesStore (наполняются SSE-событиями очередей). Пустые очереди — empty.
const q = useQueuesStore()

function str(v: unknown): string {
  return v == null ? "" : String(v)
}

const KNOWN_STATUSES = ["queued", "running", "done", "failed", "cancelled", "paused"] as const
type KnownStatus = (typeof KNOWN_STATUSES)[number]
const statusText = (status: string) =>
  (KNOWN_STATUSES as readonly string[]).includes(status) ? t(`agents.conform.status.${status as KnownStatus}`) : status
const statusClass = (status: string) =>
  ({ done: "pill-success", failed: "pill-danger", running: "pill-primary", cancelled: "pill-secondary" })[status] ?? "pill-info"

function resultText(r: ConformResult): string {
  if (r.error) return t("agents.conform.result.failed", { error: r.error })
  if (r.skipped) return t("agents.conform.result.skipped")
  return t("agents.conform.result.ok", {
    resid: r.resid_ms == null ? "—" : r.resid_ms.toFixed(1),
    coverage: r.coverage == null ? "—" : Math.round(r.coverage * 100),
  })
}
const resultClass = (r: ConformResult) =>
  r.error || r.critical.length ? "pill-danger" : r.suspect || r.warnings.length ? "pill-info" : "pill-success"
</script>

<template>
  <div class="settings-agents">
    <!-- Очередь Агента Обработки -->
    <section class="queue-section">
      <h6 class="queue-title-bar"><i class="pi pi-cog"></i> Очередь Агента Обработки</h6>
      <div v-if="!q.agent.length" class="queue-empty">Очередь пуста</div>
      <div v-else class="composition-cards-container">
        <div v-for="(t, i) in q.agent" :key="str(t.hash) || i" class="card-final card-queue">
          <div class="queue-row">
            <span class="queue-title">Серия {{ str(t.series_id) }}</span>
            <div class="pill"><i class="pi pi-link"></i> Торрент: {{ str(t.torrent_id) }}</div>
            <div class="pill"><i class="pi pi-key"></i> {{ str(t.hash).slice(0, 8) }}…</div>
            <div class="pill pill-info">{{ str(t.stage) }}</div>
          </div>
        </div>
      </div>
    </section>

    <!-- Очередь Агента Загрузки (yt-dlp) -->
    <section class="queue-section">
      <h6 class="queue-title-bar"><i class="pi pi-download"></i> Очередь Агента Загрузки (yt-dlp)</h6>
      <div v-if="!q.downloads.length" class="queue-empty">Очередь загрузок пуста</div>
      <div v-else class="composition-cards-container">
        <div v-for="(t, i) in q.downloads" :key="str(t.id) || i" class="card-final card-queue">
          <div class="queue-row">
            <span class="queue-title">{{ str(t.save_path).split(/[\\/]/).pop() }}</span>
            <div class="pill pill-primary">{{ str(t.status) }}</div>
          </div>
          <div v-if="['downloading', 'processing', 'completed'].includes(str(t.status))" class="queue-row">
            <div class="progress">
              <div class="progress-bar progress-bar-striped progress-bar-animated" :style="{ width: str(t.progress) + '%' }">{{ str(t.progress) }}%</div>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- Очередь Агента Нарезки (ffmpeg) -->
    <section class="queue-section">
      <h6 class="queue-title-bar"><i class="pi pi-clone"></i> Очередь Агента Нарезки (ffmpeg)</h6>
      <div v-if="!q.slicing.length" class="queue-empty">Очередь нарезки пуста</div>
      <div v-else class="composition-cards-container">
        <div v-for="(t, i) in q.slicing" :key="str(t.id) || i" class="card-final card-queue">
          <div class="queue-row">
            <span class="queue-title">Серия {{ str(t.series_id) }}</span>
            <div class="pill pill-info">{{ str(t.status) }}</div>
          </div>
        </div>
      </div>
    </section>

    <!-- Alignment queue: the daemon's picture, pushed by the core -->
    <section class="queue-section">
      <h6 class="queue-title-bar"><i class="pi pi-sliders-h"></i> {{ t("agents.conform.title") }}</h6>
      <div v-if="!q.conform || !q.conform.daemon.up" class="queue-empty">
        {{ q.conform && q.conform.daemon.error
          ? t("agents.conform.daemon.down", { error: q.conform.daemon.error })
          : t("agents.conform.daemon.idle") }}
      </div>
      <template v-else>
        <div class="queue-row conform-daemon">
          <div class="pill pill-success">
            {{ t("agents.conform.daemon.up", { version: str(q.conform.daemon.version), device: str(q.conform.daemon.gpu_name || q.conform.daemon.device) }) }}
          </div>
        </div>
        <div v-if="!q.conform.tasks.length" class="queue-empty">{{ t("agents.conform.empty") }}</div>
        <div v-else class="composition-cards-container">
          <div v-for="task in q.conform.tasks" :key="task.id" class="card-final card-queue">
            <div class="queue-row">
              <span class="queue-title">{{ t("agents.conform.task", { label: task.label }) }}</span>
              <div class="pill" :class="statusClass(task.status)">{{ statusText(task.status) }}</div>
              <div v-if="task.stage" class="pill pill-info">{{ t("agents.conform.stage", { stage: task.stage, pct: Math.round(task.stage_pct * 100) }) }}</div>
              <div v-if="task.dub_total && task.status === 'running'" class="pill">
                {{ t("agents.conform.dub", { index: task.dub_index, total: task.dub_total, name: task.cur_dub }) }}
              </div>
            </div>
            <div v-if="task.status === 'running'" class="queue-row">
              <div class="progress">
                <div class="progress-bar progress-bar-striped progress-bar-animated" :style="{ width: Math.round(task.progress * 100) + '%' }">{{ Math.round(task.progress * 100) }}%</div>
              </div>
            </div>
            <div v-if="task.error" class="queue-row"><div class="pill pill-danger">{{ task.error }}</div></div>
            <div v-for="r in task.results" :key="r.dub" class="queue-row">
              <span class="queue-title">{{ r.dub }}</span>
              <div class="pill" :class="resultClass(r)">{{ resultText(r) }}</div>
              <div v-if="r.suspect" class="pill pill-info">{{ t("agents.conform.result.suspect") }}</div>
              <div v-if="r.critical.length" class="pill pill-danger">{{ t("agents.conform.result.critical", { reasons: r.critical.join("; ") }) }}</div>
              <div v-if="r.warnings.length" class="pill pill-info">{{ t("agents.conform.result.warnings", { reasons: r.warnings.join("; ") }) }}</div>
            </div>
          </div>
        </div>
      </template>
    </section>

    <!-- Активные статусы торрентов (мониторинг) -->
    <section class="queue-section">
      <h6 class="queue-title-bar"><i class="pi pi-wifi"></i> Активные статусы торрентов (Мониторинг)</h6>
      <div v-if="!q.torrents.length" class="queue-empty">Нет активных торрентов для мониторинга</div>
      <div v-else class="composition-cards-container">
        <div v-for="(t, i) in q.torrents" :key="str(t.task_key) || i" class="card-final card-queue">
          <div class="queue-row">
            <span class="queue-title">{{ str(t.series_name) }}</span>
            <div class="pill"><i class="pi pi-key"></i> {{ str(t.task_key).slice(0, 8) }}…</div>
            <div class="pill pill-primary">{{ str(t.status) }}</div>
          </div>
          <div class="queue-row">
            <div class="progress">
              <div class="progress-bar progress-bar-striped progress-bar-animated" :style="{ width: str(t.progress) + '%' }">{{ str(t.progress) }}%</div>
            </div>
          </div>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.queue-section { margin-bottom: 24px; }
.queue-section:last-child { margin-bottom: 0; }
.queue-title-bar {
  display: flex; align-items: center; gap: 8px;
  font-size: 14px; font-weight: 600; color: var(--color-gray-700, #495057);
  margin: 0 0 10px; padding-bottom: 8px;
  border-bottom: 1px solid var(--border-color);
}
.queue-empty {
  padding: 16px; text-align: center; color: var(--text-muted);
  background: var(--bg-light); border-radius: var(--border-radius);
}
</style>
