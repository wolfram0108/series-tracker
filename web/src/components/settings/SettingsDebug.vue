<script setup lang="ts">
import { ref, computed, onMounted } from "vue"
import Button from "primevue/button"
import ToggleSwitch from "primevue/toggleswitch"
import InputText from "primevue/inputtext"
import { useToast } from "primevue/usetoast"
import StGroup from "../StGroup.vue"
import StSelect from "../StSelect.vue"
import DatabaseViewerModal from "./DatabaseViewerModal.vue"
import SettingsConformServer from "./SettingsConformServer.vue"
import { api } from "../../api/client"
import { sanitizePath } from "../../lib/fsnames"
import { t, type MessageKey } from "../../i18n"
import { useApi } from "../../composables/useApi"
import { useConfirm } from "../../composables/useConfirm"
import { useScannerStore } from "../../stores/scanner"
import { reasonText } from "../../lib/reason"

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
  // Контракт gateway (api_system): поля enabled/interval (НЕ scanner_enabled/
  // scan_interval) — иначе тумблер автоскана молча не срабатывает.
  await request(
    api.POST("/api/scanner/settings", {
      body: { enabled: enabled.value, interval: Number(interval.value) },
    } as never),
    { errorMessage: t("debug.autoSync.saveFailed") },
  )
}
async function scanAll() {
  await request(api.POST("/api/scanner/scan_all", {} as never), { errorMessage: t("debug.syncAll.failed") })
}

// --- флаги конвейера (GET/POST → {enabled}) ---------------------------
const forceReplace = ref(false)
const lessStrict = ref(false)
const slicingDelete = ref(false)
const conformClear = ref(false)

async function loadFlag(path: string): Promise<boolean> {
  const d = (await request(api.GET(path as never))) as { enabled?: boolean } | null
  return !!d?.enabled
}
async function saveFlag(path: string, value: boolean, label: string) {
  const ok = await request(api.POST(path as never, { body: { enabled: value } } as never), {
    errorMessage: t("debug.flag.saveFailed"),
  })
  if (ok !== null) toast.add({ severity: "info", summary: label, detail: value ? t("common.enabled") : t("common.disabled"), life: 2000 })
}

// --- числовые параметры yt-dlp (GET/POST → {value}) -------------------
// --- пределы загрузок по способу (downloads, limits.py; GET/POST /api/settings/download_limits) ----
interface DownloadLimits { threads_total: number; threads_per_file: number; single_at_once: number; multi_at_once: number }
const limits = ref({ threads_total: "32", threads_per_file: "16", single_at_once: "4" })
const multiAtOnce = ref(2)

function showLimits(got: DownloadLimits) {
  limits.value = { threads_total: String(got.threads_total), threads_per_file: String(got.threads_per_file),
                   single_at_once: String(got.single_at_once) }
  multiAtOnce.value = got.multi_at_once
}
async function loadLimits() {
  const got = (await request(api.GET("/api/settings/download_limits" as never))) as DownloadLimits | null
  if (got) showLimits(got)
}
async function saveLimits() {
  const body = { threads_total: limits.value.threads_total.trim(), threads_per_file: limits.value.threads_per_file.trim(),
                 single_at_once: limits.value.single_at_once.trim() }
  const saved = (await request(api.POST("/api/settings/download_limits" as never, { body } as never))) as DownloadLimits | null
  if (saved !== null) {
    showLimits(saved)
    toast.add({ severity: "info", summary: t("debug.downloads.saved"), life: 2000 })
  } else {
    await loadLimits()
  }
}

async function loadNum(path: string, fallback: number): Promise<string> {
  const d = (await request(api.GET(path as never))) as { value?: number } | null
  return String(d?.value ?? fallback)
}

// --- срок актуальности опроса источников (sources, term.py; GET/POST → {value}) ----
const sourcesTerm = ref("1")

async function saveSourcesTerm() {
  const saved = (await request(
    api.POST("/api/settings/sources_term" as never, { body: { value: sourcesTerm.value.trim() } } as never),
  )) as { value?: number } | null
  if (saved !== null) {
    toast.add({ severity: "info", summary: t("debug.sourcesTerm.label"), detail: String(saved.value), life: 2000 })
  }
  sourcesTerm.value = await loadNum("/api/settings/sources_term", 1)
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
  const v = sanitizePath(newPath.value)
  if (!v) return
  const ok = await request(
    api.POST("/api/settings/saved_paths", { body: { path: v } } as never),
    { errorMessage: t("debug.paths.addFailed") },
  )
  if (ok !== null) {
    newPath.value = ""
    await loadPaths()
  }
}
async function removePath(id: number) {
  const ok = await request(
    api.DELETE("/api/settings/saved_paths", { body: { id } } as never),
    { errorMessage: t("debug.paths.removeFailed") },
  )
  if (ok !== null) await loadPaths()
}

// --- база данных ------------------------------------------------------
const tables = ref<string[]>([])
const selectedTable = ref("")
const showDb = ref(false)

const TABLE_DESCRIPTION_KEYS: Record<string, MessageKey> = {
  series: "debug.db.table.series",
  torrents: "debug.db.table.torrents",
  media_items: "debug.db.table.media_items",
  download_tasks: "debug.db.table.download_tasks",
  slicing_tasks: "debug.db.table.slicing_tasks",
  sliced_files: "debug.db.table.sliced_files",
  advanced_renaming_patterns: "debug.db.table.advanced_renaming_patterns",
  renaming_patterns: "debug.db.table.renaming_patterns",
  season_patterns: "debug.db.table.season_patterns",
  settings: "debug.db.table.settings",
}

const tableOptions = computed(() => [
  { label: t("debug.db.chooseTable"), value: "" },
  ...tables.value.map((name) => ({ label: name, value: name })),
])
const tableDescription = computed(() => {
  if (!selectedTable.value) return t("debug.db.describeHint")
  const key = TABLE_DESCRIPTION_KEYS[selectedTable.value]
  return key ? t(key) : t("debug.db.table.other", { table: selectedTable.value })
})

async function loadTables() {
  const data = (await request(api.GET("/api/database/tables"), {
    errorMessage: t("dbViewer.tablesFailed"),
  })) as string[] | null
  if (data) tables.value = data
}

async function clearTable() {
  if (!selectedTable.value) return
  const r = await confirm.open({
    title: t("debug.db.clear.title"),
    message: t("debug.db.clear.message", { table: selectedTable.value }),
  })
  if (!r.confirmed) return
  const data = (await request(
    api.POST("/api/database/clear_table", { body: { table_name: selectedTable.value } } as never),
    { errorMessage: t("debug.db.clear.failed") },
  )) as { success?: boolean; message?: string } | null
  if (data?.success) {
    toast.add({ severity: "success", summary: t("debug.db.toast"), detail: reasonText(data.message) || t("debug.db.clear.done"), life: 3000 })
  }
}

onMounted(async () => {
  syncFromStore()
  void loadPaths()
  void loadTables()
  forceReplace.value = await loadFlag("/api/settings/force_replace")
  lessStrict.value = await loadFlag("/api/settings/less_strict_scan")
  slicingDelete.value = await loadFlag("/api/settings/slicing_delete_source")
  conformClear.value = await loadFlag("/api/settings/conform_clear_finished")
  void loadLimits()
  sourcesTerm.value = await loadNum("/api/settings/sources_term", 1)
})
</script>

<template>
  <div class="settings-debug">
    <!-- Сканер -->
    <div class="modern-fieldset">
      <div class="fieldset-header"><i class="pi pi-th-large"></i> {{ t("debug.sync.title") }}</div>
      <div class="fieldset-content debug-scanner">
        <label class="debug-switch">
          <ToggleSwitch v-model="enabled" @change="saveScanner" />
          <span>{{ t("series.autoSync") }}</span>
        </label>
        <div class="debug-interval">
          <span>{{ t("debug.autoSync.interval") }}</span>
          <InputText v-model="interval" inputmode="numeric" class="interval-input" @change="saveScanner" />
        </div>
        <Button
          :label="t('debug.syncAll.button')"
          icon="pi pi-refresh"
          :disabled="scanner.status?.is_scanning || scanner.status?.is_awaiting_tasks"
          @click="scanAll"
        />
      </div>
    </div>

    <!-- Параметры конвейера и отладка -->
    <div class="modern-fieldset">
      <div class="fieldset-header"><i class="pi pi-sliders-h"></i> {{ t("debug.pipeline.title") }}</div>
      <div class="fieldset-content">
        <div class="flags-grid">
          <label class="debug-switch" :title="t('debug.flag.forceReplace.hint')">
            <ToggleSwitch v-model="forceReplace" @change="saveFlag('/api/settings/force_replace', forceReplace, t('debug.flag.forceReplace'))" />
            <span>{{ t("debug.flag.forceReplace") }}</span>
          </label>
          <label class="debug-switch" :title="t('debug.flag.lessStrict.hint')">
            <ToggleSwitch v-model="lessStrict" @change="saveFlag('/api/settings/less_strict_scan', lessStrict, t('debug.flag.lessStrict'))" />
            <span>{{ t("debug.flag.lessStrict") }}</span>
          </label>
          <label class="debug-switch" :title="t('debug.flag.slicingDelete.hint')">
            <ToggleSwitch v-model="slicingDelete" @change="saveFlag('/api/settings/slicing_delete_source', slicingDelete, t('debug.flag.slicingDelete'))" />
            <span>{{ t("debug.flag.slicingDelete") }}</span>
          </label>
          <label class="debug-switch" :title="t('conform.settings.clearFinishedHint')">
            <ToggleSwitch v-model="conformClear" @change="saveFlag('/api/settings/conform_clear_finished', conformClear, t('conform.settings.clearFinished'))" />
            <span>{{ t("conform.settings.clearFinished") }}</span>
          </label>
        </div>
        <div class="nums-row">
          <div class="num-field" :title="t('debug.downloads.threadsTotalHint')">
            <span>{{ t("debug.downloads.threadsTotal") }}</span>
            <InputText v-model="limits.threads_total" inputmode="numeric" class="num-input" @change="saveLimits" />
          </div>
          <div class="num-field">
            <span>{{ t("debug.downloads.threadsPerFile") }}</span>
            <InputText v-model="limits.threads_per_file" inputmode="numeric" class="num-input" @change="saveLimits" />
          </div>
          <div class="num-field" :title="t('debug.downloads.singleAtOnceHint')">
            <span>{{ t("debug.downloads.singleAtOnce") }}</span>
            <InputText v-model="limits.single_at_once" inputmode="numeric" class="num-input" @change="saveLimits" />
          </div>
          <div class="num-field muted-hint">{{ t("debug.downloads.multiAtOnce", { count: multiAtOnce }) }}</div>
          <div class="num-field" :title="t('debug.sourcesTerm.hint')">
            <span>{{ t("debug.sourcesTerm.label") }}</span>
            <InputText v-model="sourcesTerm" inputmode="numeric" class="num-input" @change="saveSourcesTerm" />
          </div>
        </div>
      </div>
    </div>

    <SettingsConformServer />

    <!-- База данных -->
    <div class="modern-fieldset">
      <div class="fieldset-header"><i class="pi pi-database"></i> {{ t("debug.db.title") }}</div>
      <div class="fieldset-content">
        <div class="db-actions-row">
          <Button :label="t('debug.db.view')" icon="pi pi-table" severity="info" @click="showDb = true" />
          <span class="muted-hint">{{ t("debug.db.viewHint") }}</span>
        </div>
        <hr class="debug-sep" />
        <p class="muted-hint danger-hint">{{ t("debug.db.clearWarning") }}</p>
        <div class="field-group">
          <StGroup>
            <StSelect v-model="selectedTable" :options="tableOptions" :placeholder="t('debug.db.chooseTable')" />
            <div class="constructor-item item-label clear-desc">{{ tableDescription }}</div>
            <div class="constructor-item item-button-group">
              <button class="btn-icon btn-delete" :title="t('debug.db.clear')" :disabled="!selectedTable" @click="clearTable">
                <i class="pi pi-trash" />
              </button>
            </div>
          </StGroup>
        </div>
      </div>
    </div>

    <!-- Сохранённые пути -->
    <div class="modern-fieldset">
      <div class="fieldset-header"><i class="pi pi-folder"></i> {{ t("savedPaths.title") }}</div>
      <div class="fieldset-content">
        <div v-if="paths.length" class="saved-paths-list">
          <div v-for="p in paths" :key="p.id" class="saved-path-row">
            <span class="saved-path-value">{{ p.path }}</span>
            <Button icon="pi pi-times" severity="danger" text rounded :title="t('common.delete')" @click="removePath(p.id)" />
          </div>
        </div>
        <div v-else class="muted-hint">{{ t("debug.paths.empty") }}</div>
        <div class="add-path-row">
          <InputText v-model="newPath" placeholder="/nas/media/..." class="add-path-input" @keyup.enter="addPath" />
          <Button :label="t('common.add')" icon="pi pi-plus" severity="secondary" @click="addPath" />
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
