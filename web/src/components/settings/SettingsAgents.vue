<script setup lang="ts">
import { useQueuesStore } from "../../stores/queues"
import { t } from "../../i18n"
import ConformQueue from "./ConformQueue.vue"
import DownloadCard from "./DownloadCard.vue"
import PackCard from "./PackCard.vue"
import PackJournal from "./PackJournal.vue"
import { api } from "../../api/client"
import { useApi } from "../../composables/useApi"
import { QBIT_STATES, SLICING_STATES, stateLabel, stateOf, TORRENT_STAGES, type StateName } from "../../lib/states"

// The tab of the agents: every queue as cards, as the core pushes them (queuesStore). A state of a queue is a state of the
// vocabulary (lib/states.ts); a code the vocabulary does not know is shown as it came.
const q = useQueuesStore()
const { request } = useApi()

// A card that stayed after its sync is removed; the core tells the queue anew (download_queue_update).
async function removeCard(id: string): Promise<void> {
  await request(api.DELETE("/api/downloads/cards/{card_id}", { params: { path: { card_id: id } } }))
}

function str(v: unknown): string {
  return v == null ? "" : String(v)
}

const stateText = (state: StateName | null, code: unknown): string => state ? stateLabel(state) : str(code)

// A card of the packing that stayed after its sync is removed; the core tells the queue anew (pack_queue_update).
async function removePack(id: string): Promise<void> {
  await request(api.DELETE("/api/pack/cards/{card_id}", { params: { path: { card_id: id } } }))
}
</script>

<template>
  <div class="settings-agents">
    <section class="queue-section">
      <h6 class="queue-title-bar"><i class="pi pi-cog"></i> {{ t("agents.processing.title") }}</h6>
      <div v-if="!q.agent.length" class="queue-empty">{{ t("agents.empty") }}</div>
      <div v-else class="composition-cards-container">
        <div v-for="(task, i) in q.agent" :key="str(task.hash) || i" class="card-final card-queue">
          <div class="queue-row">
            <span class="queue-title">{{ t("agents.series", { id: str(task.series_id) }) }}</span>
            <div class="pill"><i class="pi pi-link"></i> {{ t("agents.torrent", { id: str(task.torrent_id) }) }}</div>
            <div class="pill"><i class="pi pi-key"></i> {{ str(task.hash).slice(0, 8) }}…</div>
            <div class="pill" :data-state="stateOf(TORRENT_STAGES, str(task.stage)) ?? undefined">
              {{ stateText(stateOf(TORRENT_STAGES, str(task.stage)), task.stage) }}
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- The downloads: a card to a need of a plan (core/contracts/downloads.py) -->
    <section class="queue-section">
      <h6 class="queue-title-bar"><i class="pi pi-download"></i> {{ t("download.title") }}</h6>
      <div v-if="!q.downloads.length" class="queue-empty">{{ t("agents.empty") }}</div>
      <div v-else class="composition-cards-container">
        <DownloadCard v-for="card in q.downloads" :key="card.id" :card="card" @remove="removeCard" />
      </div>
    </section>

    <section class="queue-section">
      <h6 class="queue-title-bar"><i class="pi pi-clone"></i> {{ t("agents.slicing.title") }}</h6>
      <div v-if="!q.slicing.length" class="queue-empty">{{ t("agents.empty") }}</div>
      <div v-else class="composition-cards-container">
        <div v-for="(task, i) in q.slicing" :key="str(task.id) || i" class="card-final card-queue">
          <div class="queue-row">
            <span class="queue-title">{{ t("agents.series", { id: str(task.series_id) }) }}</span>
            <div class="pill" :data-state="stateOf(SLICING_STATES, str(task.status)) ?? undefined">
              {{ stateText(stateOf(SLICING_STATES, str(task.status)), task.status) }}
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- Packing queue: the containers of the episodes, pushed by the core -->
    <section class="queue-section">
      <h6 class="queue-title-bar"><i class="pi pi-box"></i> {{ t("agents.pack.title") }}</h6>
      <div v-if="!q.pack.length && !q.journals.length" class="queue-empty">{{ t("agents.empty") }}</div>
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

    <section class="queue-section">
      <h6 class="queue-title-bar"><i class="pi pi-wifi"></i> {{ t("agents.torrents.title") }}</h6>
      <div v-if="!q.torrents.length" class="queue-empty">{{ t("agents.empty") }}</div>
      <div v-else class="composition-cards-container">
        <div v-for="(task, i) in q.torrents" :key="str(task.qb_hash) || i" class="card-final card-queue">
          <div class="queue-row">
            <span class="queue-title">{{ str(task.series_name) }}</span>
            <div class="pill"><i class="pi pi-key"></i> {{ str(task.qb_hash).slice(0, 8) }}…</div>
            <div class="pill" :data-state="stateOf(QBIT_STATES, str(task.status)) ?? undefined">
              {{ stateText(stateOf(QBIT_STATES, str(task.status)), task.status) }}
            </div>
          </div>
          <div class="queue-row">
            <div class="progress">
              <div class="progress-bar progress-bar-striped progress-bar-animated" :style="{ width: str(task.progress) + '%' }">{{ str(task.progress) }}%</div>
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
