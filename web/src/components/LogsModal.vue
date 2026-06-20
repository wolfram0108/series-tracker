<script setup lang="ts">
import { ref, onMounted } from "vue"
import Select from "primevue/select"
import InputText from "primevue/inputtext"
import ModalShell from "./ModalShell.vue"
import LogsLoggingTab from "./LogsLoggingTab.vue"
import { api } from "../api/client"
import { useApi } from "../composables/useApi"

// Модалка логов: две вкладки — «Просмотр» (фильтры группа/уровень/лимит +
// таблица с подсветкой по уровню, GET /api/logs) и «Настройка» (детализация
// логирования по модулям + флаги дампа, LogsLoggingTab).
defineEmits<{ (e: "close"): void }>()

const tab = ref<"viewer" | "settings">("viewer")
const TABS = [
  { label: "Просмотр", value: "viewer", icon: "pi-list" },
  { label: "Настройка", value: "settings", icon: "pi-sliders-h" },
] as const

interface LogEntry { timestamp: string; group: string; level: string; message: string }
const logs = ref<LogEntry[]>([])
// сентинел "all": PrimeVue Select трактует пустую строку как «не выбрано» и
// не показывает опцию-«все» → дефолт делаем реальным значением, в запрос —
// undefined (без фильтра).
const ALL = "all"
const group = ref(ALL)
const level = ref(ALL)
const limit = ref("200")
const { request } = useApi()

const GROUPS = [
  "auth", "agent", "db", "downloader_agent", "kinozal_parser", "monitoring_agent",
  "parser_api", "qbittorrent", "renaming_agent", "renaming_processor", "run", "scanner",
  "series_api", "slicing_agent", "smart_collector", "system_api", "vk_scraper",
]
const groupOptions = [{ label: "Все группы", value: ALL }, ...[...GROUPS].sort().map((g) => ({ label: g, value: g }))]
const levelOptions = [
  { label: "Все уровни", value: ALL },
  { label: "DEBUG", value: "DEBUG" },
  { label: "INFO", value: "INFO" },
  { label: "WARNING", value: "WARNING" },
  { label: "ERROR", value: "ERROR" },
]

async function loadLogs() {
  const data = (await request(
    api.GET("/api/logs", {
      params: {
        query: {
          group: group.value === ALL ? undefined : group.value,
          level: level.value === ALL ? undefined : level.value,
          limit: Number(limit.value) || 200,
        },
      },
    } as never),
  )) as unknown
  if (Array.isArray(data)) logs.value = data as LogEntry[]
}

function rowClass(lvl: string): string {
  return { INFO: "log-info", DEBUG: "log-debug", WARNING: "log-warning", ERROR: "log-error" }[lvl] ?? ""
}
function fmt(iso: string): string {
  if (!iso) return "—"
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString("ru-RU")
}

onMounted(loadLogs)
</script>

<template>
  <ModalShell size="xl" fixed-height @close="$emit('close')">
    <template #title><i class="pi pi-book"></i> Логи</template>
    <template #header-extra>
      <div class="header-tabs st-tabs">
        <button
          v-for="t in TABS"
          :key="t.value"
          class="st-tab"
          :class="{ active: tab === t.value }"
          :title="t.label"
          @click="tab = t.value"
        >
          <i class="pi tab-icon" :class="t.icon"></i>
          <span class="tab-label" :data-text="t.label"><span class="tl-text">{{ t.label }}</span></span>
        </button>
      </div>
    </template>

    <template v-if="tab === 'viewer'">
      <div class="logs-filters">
        <Select v-model="group" :options="groupOptions" option-label="label" option-value="value" @change="loadLogs" />
        <Select v-model="level" :options="levelOptions" option-label="label" option-value="value" @change="loadLogs" />
        <InputText v-model="limit" inputmode="numeric" class="limit-input" title="Лимит строк" @change="loadLogs" />
      </div>

      <div class="div-table logs-table">
        <div class="div-table-header">
          <div class="div-table-cell">Время</div>
          <div class="div-table-cell">Группа</div>
          <div class="div-table-cell">Уровень</div>
          <div class="div-table-cell">Сообщение</div>
        </div>
        <div class="div-table-body">
          <div v-for="(l, i) in logs" :key="i" class="div-table-row" :class="rowClass(l.level)">
            <div class="div-table-cell">{{ fmt(l.timestamp) }}</div>
            <div class="div-table-cell">{{ l.group }}</div>
            <div class="div-table-cell">{{ l.level }}</div>
            <div class="div-table-cell msg">{{ l.message }}</div>
          </div>
          <div v-if="!logs.length" class="div-table-row"><div class="div-table-cell empty">Логов нет</div></div>
        </div>
      </div>
    </template>

    <LogsLoggingTab v-else />
  </ModalShell>
</template>

<style scoped>
.logs-filters { display: flex; gap: 12px; margin-bottom: 16px; flex-wrap: wrap; }
.limit-input { width: 100px; }
.logs-table :deep(.div-table-header),
.logs-table :deep(.div-table-row) {
  display: grid; grid-template-columns: 170px 150px 90px 1fr;
}
.logs-table .msg { font-family: var(--font-mono); white-space: normal; word-break: break-word; }
.logs-table .empty { grid-column: 1 / -1; text-align: center; color: var(--text-muted); }
/* подсветка строк по уровню */
.logs-table .log-info { background: rgba(13, 110, 253, 0.04); }
.logs-table .log-debug { color: var(--text-muted); }
.logs-table .log-warning { background: rgba(255, 193, 7, 0.12); }
.logs-table .log-error { background: rgba(220, 53, 69, 0.1); }
</style>
