<script setup lang="ts">
import { ref, onMounted } from "vue"
import Button from "primevue/button"
import ModalShell from "../ModalShell.vue"
import { api } from "../../api/client"
import { useApi } from "../../composables/useApi"

// Полноэкранное окно просмотра БД (порт DatabaseViewerModal): список
// таблиц вкладками + таблица строк выбранной таблицы. Только чтение.
// Контракт: GET /api/database/tables → [имена]; /table/{name} → [строки].

const emit = defineEmits<{ (e: "close"): void }>()

const { request } = useApi()
const tables = ref<string[]>([])
const activeTable = ref<string | null>(null)
const rows = ref<Record<string, unknown>[]>([])
const headers = ref<string[]>([])
const loading = ref(false)

async function fetchTables() {
  loading.value = true
  const data = (await request(api.GET("/api/database/tables"), {
    errorMessage: "Ошибка загрузки списка таблиц",
  })) as string[] | null
  loading.value = false
  if (data) {
    tables.value = data
    if (data.length) await fetchTable(data[0])
  }
}

async function fetchTable(name: string) {
  activeTable.value = name
  loading.value = true
  rows.value = []
  headers.value = []
  const data = (await request(
    api.GET("/api/database/table/{table_name}", {
      params: { path: { table_name: name } },
    } as never),
    { errorMessage: `Ошибка загрузки таблицы ${name}` },
  )) as Record<string, unknown>[] | null
  loading.value = false
  if (data) {
    rows.value = data
    if (data.length) headers.value = Object.keys(data[0])
  }
}

function cell(v: unknown): string {
  if (v === null || v === undefined) return ""
  return typeof v === "object" ? JSON.stringify(v) : String(v)
}

onMounted(fetchTables)
</script>

<template>
  <ModalShell size="full" fixed-height @close="emit('close')">
    <template #title><i class="pi pi-database"></i> Просмотр базы данных</template>

    <div class="db-viewer">
      <div class="db-tabs">
        <button
          v-for="t in tables"
          :key="t"
          class="db-tab"
          :class="{ active: activeTable === t }"
          @click="fetchTable(t)"
        >
          {{ t }}
        </button>
      </div>

      <div class="db-content">
        <div v-if="loading" class="db-loading"><i class="pi pi-spin pi-spinner" style="font-size: 1.6rem" /></div>
        <div v-else-if="!rows.length" class="db-empty">
          <i class="pi pi-inbox" />
          <p>В таблице «{{ activeTable }}» нет данных.</p>
        </div>
        <div v-else class="db-table-wrap">
          <table class="db-table">
            <thead>
              <tr>
                <th v-for="h in headers" :key="h">{{ h }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(row, i) in rows" :key="i">
                <td v-for="h in headers" :key="h" :title="cell(row[h])">
                  <div class="db-cell">{{ cell(row[h]) }}</div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <template #footer>
      <Button label="Закрыть" icon="pi pi-times" severity="secondary" @click="emit('close')" />
    </template>
  </ModalShell>
</template>

<style scoped>
.db-viewer { display: flex; flex-direction: column; height: 100%; min-height: 0; gap: 12px; }
.db-tabs { display: flex; flex-wrap: wrap; gap: 6px; flex-shrink: 0; }
.db-tab {
  padding: 5px 12px; border: 1px solid var(--border-color); background: #fff;
  border-radius: 6px; cursor: pointer; font-size: 0.85rem; color: var(--color-text);
  font-family: var(--font-mono); transition: all 0.15s ease;
}
.db-tab:hover { background: var(--bg-light); }
.db-tab.active { background: #cfe2ff; border-color: #9ec5fe; color: #0c63e4; font-weight: 600; }
.db-content { flex: 1 1 auto; min-height: 0; overflow: auto; border: 1px solid var(--border-color); border-radius: 8px; }
.db-loading, .db-empty {
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: 10px; height: 100%; color: var(--text-muted); padding: 2rem;
}
.db-empty .pi { font-size: 2rem; opacity: 0.5; }
.db-empty p { margin: 0; }
.db-table-wrap { min-width: 100%; }
.db-table { width: 100%; border-collapse: collapse; font-size: 0.82rem; }
.db-table th, .db-table td { border: 1px solid var(--border-color); padding: 5px 8px; text-align: left; vertical-align: top; }
.db-table thead th {
  position: sticky; top: 0; z-index: 1; background: var(--bg-light);
  font-weight: 600; white-space: nowrap;
}
.db-table tbody tr:nth-child(even) { background: #fafbfc; }
.db-cell { max-width: 360px; max-height: 90px; overflow: auto; font-family: var(--font-mono); white-space: pre-wrap; word-break: break-word; }
</style>
