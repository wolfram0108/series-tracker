<script setup lang="ts">
import { useQueuesStore } from "../../stores/queues"

// Вкладка «Агенты»: 4 очереди карточками (вместо таблиц). Данные — из
// queuesStore (наполняются SSE-событиями очередей). Пустые очереди — empty.
const q = useQueuesStore()

function str(v: unknown): string {
  return v == null ? "" : String(v)
}
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
