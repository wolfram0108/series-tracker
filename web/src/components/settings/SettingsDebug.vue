<script setup lang="ts">
import { ref, computed, onMounted } from "vue"
import Button from "primevue/button"
import ToggleSwitch from "primevue/toggleswitch"
import InputText from "primevue/inputtext"
import { useToast } from "primevue/usetoast"
import StGroup from "../StGroup.vue"
import StSelect from "../StSelect.vue"
import DatabaseViewerModal from "./DatabaseViewerModal.vue"
import { api } from "../../api/client"
import { useApi } from "../../composables/useApi"
import { useConfirm } from "../../composables/useConfirm"
import { useScannerStore } from "../../stores/scanner"

// Вкладка «Отладка»: сканер, сохранённые пути, флаги/параметры конвейера,
// просмотр и очистка БД. Контракт проверен по modules/gateway/api_settings.py.
const scanner = useScannerStore()
const { request } = useApi()
const confirm = useConfirm()
const toast = useToast()

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

// --- флаги конвейера (GET/POST → {enabled}) ---------------------------
const forceReplace = ref(false)
const lessStrict = ref(false)
const slicingDelete = ref(false)

async function loadFlag(path: string): Promise<boolean> {
  const d = (await request(api.GET(path as never))) as { enabled?: boolean } | null
  return !!d?.enabled
}
async function saveFlag(path: string, value: boolean, label: string) {
  const ok = await request(api.POST(path as never, { body: { enabled: value } } as never), {
    errorMessage: "Ошибка сохранения настройки",
  })
  if (ok !== null) toast.add({ severity: "info", summary: label, detail: value ? "включено" : "выключено", life: 2000 })
}

// --- числовые параметры yt-dlp (GET/POST → {value}) -------------------
const parallelDownloads = ref("2")
const concurrentFragments = ref("6")

async function loadNum(path: string, fallback: number): Promise<string> {
  const d = (await request(api.GET(path as never))) as { value?: number } | null
  return String(d?.value ?? fallback)
}
async function saveNum(path: string, value: string, label: string) {
  const n = Number(String(value).replace(/[^0-9]/g, "")) || 0
  const ok = await request(api.POST(path as never, { body: { value: n } } as never), {
    errorMessage: "Ошибка сохранения параметра",
  })
  if (ok !== null) toast.add({ severity: "info", summary: label, detail: String(n), life: 2000 })
}

// сохранённые пути
interface SavedPath { id: number; path: string }
const paths = ref<SavedPath[]>([])
const newPath = ref("")

async function loadPaths() {
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

// --- база данных ------------------------------------------------------
const tables = ref<string[]>([])
const selectedTable = ref("")
const showDb = ref(false)

const TABLE_DESCRIPTIONS: Record<string, string> = {
  series: "Удалить все отслеживаемые сериалы.",
  torrents: "Удалить все связанные торренты из БД (не из qBittorrent).",
  media_items: "Удалить все найденные медиа-элементы (для VK-сериалов).",
  download_tasks: "Очистить текущую очередь загрузок для VK-видео.",
  slicing_tasks: "Очистить очередь задач на нарезку видео.",
  sliced_files: "Удалить все записи о нарезанных файлах.",
  advanced_renaming_patterns: "Сбросить все продвинутые паттерны переименования.",
  renaming_patterns: "Сбросить все паттерны переименования эпизодов.",
  season_patterns: "Сбросить все паттерны переименования сезонов.",
  settings: "Удалить все сохранённые настройки, включая SID и флаги отладки.",
}

const tableOptions = computed(() => [
  { label: "Выберите таблицу…", value: "" },
  ...tables.value.map((t) => ({ label: t, value: t })),
])
const tableDescription = computed(() =>
  selectedTable.value
    ? TABLE_DESCRIPTIONS[selectedTable.value] || `Очистить таблицу «${selectedTable.value}».`
    : "Выберите таблицу для описания.",
)

async function loadTables() {
  const data = (await request(api.GET("/api/database/tables"), {
    errorMessage: "Ошибка загрузки списка таблиц",
  })) as string[] | null
  if (data) tables.value = data
}

async function clearTable() {
  if (!selectedTable.value) return
  const r = await confirm.open({
    title: "Очистка таблицы",
    message: `ВНИМАНИЕ! Удалить ВСЕ записи из таблицы «${selectedTable.value}»? Действие необратимо.`,
  })
  if (!r.confirmed) return
  const data = (await request(
    api.POST("/api/database/clear_table", { body: { table_name: selectedTable.value } } as never),
    { errorMessage: "Ошибка очистки таблицы" },
  )) as { success?: boolean; message?: string } | null
  if (data?.success) {
    toast.add({ severity: "success", summary: "БД", detail: data.message || "Таблица очищена", life: 3000 })
  }
}

onMounted(async () => {
  syncFromStore()
  void loadPaths()
  void loadTables()
  forceReplace.value = await loadFlag("/api/settings/force_replace")
  lessStrict.value = await loadFlag("/api/settings/less_strict_scan")
  slicingDelete.value = await loadFlag("/api/settings/slicing_delete_source")
  parallelDownloads.value = await loadNum("/api/settings/parallel_downloads", 2)
  concurrentFragments.value = await loadNum("/api/settings/concurrent_fragments", 6)
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

    <!-- Параметры конвейера и отладка -->
    <div class="modern-fieldset">
      <div class="fieldset-header"><i class="pi pi-sliders-h"></i> Параметры конвейера</div>
      <div class="fieldset-content">
        <div class="flags-grid">
          <label class="debug-switch" title="Считать все существующие торренты устаревшими и пытаться заменить при любом сканировании.">
            <ToggleSwitch v-model="forceReplace" @change="saveFlag('/api/settings/force_replace', forceReplace, 'Всегда заменять торренты')" />
            <span>Всегда заменять торренты</span>
          </label>
          <label class="debug-switch" title="Проверять наличие файлов на диске, даже если их нет в БД: найденный файл регистрируется, а не скачивается заново.">
            <ToggleSwitch v-model="lessStrict" @change="saveFlag('/api/settings/less_strict_scan', lessStrict, 'Менее строгий скан')" />
            <span>Менее строгий режим сканирования</span>
          </label>
          <label class="debug-switch" title="Агент нарезки удалит исходный файл-компиляцию после успешной нарезки.">
            <ToggleSwitch v-model="slicingDelete" @change="saveFlag('/api/settings/slicing_delete_source', slicingDelete, 'Удалять исходник после нарезки')" />
            <span>Удалять исходник после нарезки</span>
          </label>
        </div>
        <div class="nums-row">
          <div class="num-field">
            <span>Максимум параллельных загрузок (yt-dlp)</span>
            <InputText
              v-model="parallelDownloads"
              inputmode="numeric"
              class="num-input"
              @change="saveNum('/api/settings/parallel_downloads', parallelDownloads, 'Параллельных загрузок')"
            />
          </div>
          <div class="num-field">
            <span>Потоков на одну загрузку (yt-dlp -N)</span>
            <InputText
              v-model="concurrentFragments"
              inputmode="numeric"
              class="num-input"
              @change="saveNum('/api/settings/concurrent_fragments', concurrentFragments, 'Потоков на загрузку')"
            />
          </div>
        </div>
      </div>
    </div>

    <!-- База данных -->
    <div class="modern-fieldset">
      <div class="fieldset-header"><i class="pi pi-database"></i> База данных</div>
      <div class="fieldset-content">
        <div class="db-actions-row">
          <Button label="Просмотр БД" icon="pi pi-table" severity="info" @click="showDb = true" />
          <span class="muted-hint">Полноэкранное окно просмотра всех таблиц базы данных.</span>
        </div>
        <hr class="debug-sep" />
        <p class="muted-hint danger-hint">Очистка таблицы необратима и удаляет все её данные.</p>
        <div class="field-group">
          <StGroup>
            <StSelect v-model="selectedTable" :options="tableOptions" placeholder="Выберите таблицу…" />
            <div class="constructor-item item-label clear-desc">{{ tableDescription }}</div>
            <div class="constructor-item item-button-group">
              <button class="btn-icon btn-delete" title="Очистить таблицу" :disabled="!selectedTable" @click="clearTable">
                <i class="pi pi-trash" />
              </button>
            </div>
          </StGroup>
        </div>
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

    <DatabaseViewerModal v-if="showDb" @close="showDb = false" />
  </div>
</template>

<style scoped>
.debug-scanner { display: flex; flex-wrap: wrap; align-items: center; gap: 20px; }
.debug-switch { display: flex; align-items: center; gap: 10px; cursor: pointer; }
.debug-interval { display: flex; align-items: center; gap: 10px; }
.interval-input { width: 90px; }
.flags-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px 24px; margin-bottom: 16px; }
.nums-row { display: flex; flex-wrap: wrap; gap: 24px; }
.num-field { display: flex; flex-direction: column; gap: 6px; }
.num-field span { color: var(--text-muted); font-size: 0.85rem; }
.num-input { width: 120px; }
.db-actions-row { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; margin-bottom: 4px; }
.debug-sep { border: none; border-top: 1px solid var(--border-color); margin: 14px 0; }
.clear-desc { flex-grow: 1; justify-content: flex-start; white-space: normal; color: var(--text-muted); font-weight: 400; }
.saved-paths-list { display: flex; flex-direction: column; gap: 6px; margin-bottom: 12px; }
.saved-path-row {
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
  padding: 6px 6px 6px 14px; background: var(--bg-light); border-radius: var(--border-radius);
}
.saved-path-value { font-family: var(--font-mono); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.muted-hint { color: var(--text-muted); margin-bottom: 12px; }
.danger-hint { color: var(--color-red-1); }
.add-path-row { display: flex; gap: 10px; }
.add-path-input { flex: 1; }
.settings-debug { display: flex; flex-direction: column; gap: 18px; }
</style>
