<script setup lang="ts">
import { ref, onMounted } from "vue"
import Button from "primevue/button"
import ToggleSwitch from "primevue/toggleswitch"
import InputText from "primevue/inputtext"
import { api } from "../../api/client"
import { useApi } from "../../composables/useApi"
import { useScannerStore } from "../../stores/scanner"

// Вкладка «Отладка»: управление сканером (вкл/интервал/скан сейчас) и
// сохранёнными путями (CRUD). Просмотр/очистка БД — отдельная под-веха.
const scanner = useScannerStore()
const { request } = useApi()

const enabled = ref(false)
const interval = ref("360")

function syncFromStore() {
  if (scanner.status) {
    enabled.value = scanner.status.scanner_enabled
    interval.value = String(scanner.status.scan_interval)
  }
}

async function saveScanner() {
  await request(
    api.POST("/api/scanner/settings", {
      body: { scanner_enabled: enabled.value, scan_interval: Number(interval.value) },
    } as never),
    { errorMessage: "Ошибка сохранения настроек сканера" },
  )
}
async function scanAll() {
  await request(api.POST("/api/scanner/scan_all", {} as never), { errorMessage: "Ошибка запуска сканирования" })
}

// сохранённые пути
interface SavedPath { id: number; path: string }
const paths = ref<SavedPath[]>([])
const newPath = ref("")

async function loadPaths() {
  // Бэк отдаёт обёртку { paths: [...] }, а не голый массив.
  const data = (await request(api.GET("/api/settings/saved_paths"))) as { paths?: SavedPath[] } | null
  if (data && Array.isArray(data.paths)) paths.value = data.paths
}
async function addPath() {
  const v = newPath.value.trim()
  if (!v) return
  const ok = await request(
    api.POST("/api/settings/saved_paths", { body: { path: v } } as never),
    { errorMessage: "Ошибка добавления пути" },
  )
  if (ok !== null) {
    newPath.value = ""
    await loadPaths()
  }
}
async function removePath(id: number) {
  const ok = await request(
    api.DELETE("/api/settings/saved_paths", { body: { id } } as never),
    { errorMessage: "Ошибка удаления пути" },
  )
  if (ok !== null) await loadPaths()
}

onMounted(() => {
  syncFromStore()
  void loadPaths()
})
</script>

<template>
  <div class="settings-debug">
    <!-- Сканер -->
    <div class="modern-fieldset">
      <div class="fieldset-header"><i class="pi pi-th-large"></i> Агент сканирования</div>
      <div class="fieldset-content debug-scanner">
        <label class="debug-switch">
          <ToggleSwitch v-model="enabled" @change="saveScanner" />
          <span>Автосканирование</span>
        </label>
        <div class="debug-interval">
          <span>Интервал (мин):</span>
          <InputText v-model="interval" inputmode="numeric" class="interval-input" @change="saveScanner" />
        </div>
        <Button
          label="Сканировать всё сейчас"
          icon="pi pi-refresh"
          :disabled="scanner.status?.is_scanning || scanner.status?.is_awaiting_tasks"
          @click="scanAll"
        />
      </div>
    </div>

    <!-- Сохранённые пути -->
    <div class="modern-fieldset">
      <div class="fieldset-header"><i class="pi pi-folder"></i> Сохранённые пути</div>
      <div class="fieldset-content">
        <div v-if="paths.length" class="saved-paths-list">
          <div v-for="p in paths" :key="p.id" class="saved-path-row">
            <span class="saved-path-value">{{ p.path }}</span>
            <Button icon="pi pi-times" severity="danger" text rounded title="Удалить" @click="removePath(p.id)" />
          </div>
        </div>
        <div v-else class="muted-hint">Сохранённых путей пока нет.</div>
        <div class="add-path-row">
          <InputText v-model="newPath" placeholder="/nas/media/..." class="add-path-input" @keyup.enter="addPath" />
          <Button label="Добавить" icon="pi pi-plus" severity="secondary" @click="addPath" />
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.debug-scanner { display: flex; flex-wrap: wrap; align-items: center; gap: 20px; }
.debug-switch { display: flex; align-items: center; gap: 10px; cursor: pointer; }
.debug-interval { display: flex; align-items: center; gap: 10px; }
.interval-input { width: 90px; }
.saved-paths-list { display: flex; flex-direction: column; gap: 6px; margin-bottom: 12px; }
.saved-path-row {
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
  padding: 6px 6px 6px 14px; background: var(--bg-light); border-radius: var(--border-radius);
}
.saved-path-value { font-family: var(--font-mono); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.muted-hint { color: var(--text-muted); margin-bottom: 12px; }
.add-path-row { display: flex; gap: 10px; }
.add-path-input { flex: 1; }
</style>
