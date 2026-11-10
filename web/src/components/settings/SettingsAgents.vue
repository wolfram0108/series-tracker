<script setup lang="ts">
import { useQueuesStore } from "../../stores/queues"
import { t } from "../../i18n"
import ConformQueue from "./ConformQueue.vue"
import DownloadCard from "./DownloadCard.vue"
import PackCard from "./PackCard.vue"
import PackJournal from "./PackJournal.vue"
import { api } from "../../api/client"
import { useApi } from "../../composables/useApi"

// Вкладка «Агенты»: 4 очереди карточками (вместо таблиц). Данные — из
// queuesStore (наполняются SSE-событиями очередей). Пустые очереди — empty.
const q = useQueuesStore()
const { request } = useApi()

// A card that stayed after its sync is removed; the core tells the queue anew (download_queue_update).
async function removeCard(id: string): Promise<void> {
  await request(api.DELETE("/api/downloads/cards/{card_id}", { params: { path: { card_id: id } } }))
}

function str(v: unknown): string {
  return v == null ? "" : String(v)
}

// A card of the packing that stayed after its sync is removed; the core tells the queue anew (pack_queue_update).
async function removePack(id: string): Promise<void> {
  await request(api.DELETE("/api/pack/cards/{card_id}", { params: { path: { card_id: id } } }))
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

    <!-- The downloads: a card to a need of a plan (core/contracts/downloads.py) -->
    <section class="queue-section">
      <h6 class="queue-title-bar"><i class="pi pi-download"></i> {{ t("download.title") }}</h6>
      <div v-if="!q.downloads.length" class="queue-empty">{{ t("download.empty") }}</div>
      <div v-else class="composition-cards-container">
        <DownloadCard v-for="card in q.downloads" :key="card.id" :card="card" @remove="removeCard" />
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

    <!-- Packing queue: the containers of the episodes, pushed by the core -->
    <section class="queue-section">
      <h6 class="queue-title-bar"><i class="pi pi-box"></i> {{ t("agents.pack.title") }}</h6>
      <div v-if="!q.pack.length && !q.journals.length" class="queue-empty">{{ t("agents.pack.empty") }}</div>
      <div v-else class="composition-cards-container">
        <PackJournal v-for="journal in q.journals" :key="`journal-${journal.series_id}`" :journal="journal" />
        <PackCard v-for="card in q.pack" :key="card.id" :card="card" @remove="removePack" />
      </div>
    </section>

    <!-- Alignment queue: the daemon's picture, pushed by the core -->
    <section class="queue-section">
      <h6 class="queue-title-bar"><i class="pi pi-sliders-h"></i> {{ t("agents.conform.title") }}</h6>
      <ConformQueue />
    </section>

    <!-- Активные статусы торрентов (мониторинг) -->
    <section class="queue-section">
      <h6 class="queue-title-bar"><i class="pi pi-wifi"></i> Активные статусы торрентов (Мониторинг)</h6>
      <div v-if="!q.torrents.length" class="queue-empty">Нет активных торрентов для мониторинга</div>
      <div v-else class="composition-cards-container">
        <div v-for="(t, i) in q.torrents" :key="str(t.qb_hash) || i" class="card-final card-queue">
          <div class="queue-row">
            <span class="queue-title">{{ str(t.series_name) }}</span>
            <div class="pill"><i class="pi pi-key"></i> {{ str(t.qb_hash).slice(0, 8) }}…</div>
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
.queue-status { flex-wrap: wrap; margin-bottom: 8px; }
.queue-status .pill { min-width: 0; max-width: 100%; overflow: hidden; text-overflow: ellipsis; }
.queue-empty {
  padding: 16px; text-align: center; color: var(--text-muted);
  background: var(--bg-light); border-radius: var(--border-radius);
}
</style>
