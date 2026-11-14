<script setup lang="ts">
import { ref, reactive, computed, onMounted, watch } from "vue"
import Button from "primevue/button"
import { useToast } from "primevue/usetoast"
import SheetField from "../SheetField.vue"
import SheetRow from "../SheetRow.vue"
import StBtn from "../StBtn.vue"
import StInput from "../StInput.vue"
import StNumber from "../StNumber.vue"
import StSelect from "../StSelect.vue"
import StSwitch from "../StSwitch.vue"
import DatabaseViewerModal from "./DatabaseViewerModal.vue"
import { api } from "../../api/client"
import { sanitizePath } from "../../lib/fsnames"
import { t, type MessageKey } from "../../i18n"
import { useApi } from "../../composables/useApi"
import { useDownloadLimits } from "../../composables/useDownloadLimits"
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
async function setEnabled(value: boolean) {
  enabled.value = value
  await saveScanner()
}
async function scanAll() {
  await request(api.POST("/api/scanner/scan_all", {} as never), { errorMessage: t("debug.syncAll.failed") })
}

// --- флаги конвейера (GET/POST → {enabled}) ---------------------------
const FLAGS = [
  { key: "forceReplace", path: "/api/settings/force_replace", label: "debug.flag.forceReplace", hint: "debug.flag.forceReplace.hint" },
  { key: "lessStrict", path: "/api/settings/less_strict_scan", label: "debug.flag.lessStrict", hint: "debug.flag.lessStrict.hint" },
  { key: "slicingDelete", path: "/api/settings/slicing_delete_source", label: "debug.flag.slicingDelete", hint: "debug.flag.slicingDelete.hint" },
] as const satisfies readonly { key: string; path: string; label: MessageKey; hint: MessageKey }[]
const flags = reactive({ forceReplace: false, lessStrict: false, slicingDelete: false })
async function setFlag(flag: (typeof FLAGS)[number], value: boolean) {
  flags[flag.key] = value
  await saveFlag(flag.path, value, t(flag.label))
}

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
// --- пределы загрузок по способу (общее состояние с окном расширений: composables/useDownloadLimits.ts) ----
const { limits: shared, load: loadLimits, save: saveShared } = useDownloadLimits()
const limits = ref({ threads_total: "32", threads_per_file: "16", single_at_once: "4" })
const LIMITS = [
  { field: "threads_total", label: "debug.downloads.threadsTotal", hint: "debug.downloads.threadsTotalHint" },
  { field: "threads_per_file", label: "debug.downloads.threadsPerFile", hint: null },
  { field: "single_at_once", label: "debug.downloads.singleAtOnce", hint: "debug.downloads.singleAtOnceHint" },
] as const satisfies readonly { field: keyof typeof limits.value; label: MessageKey; hint: MessageKey | null }[]
const multiAtOnce = computed(() => shared.value?.multi_at_once ?? 2)
watch(shared, (got) => {
  if (got) limits.value = { threads_total: String(got.threads_total), threads_per_file: String(got.threads_per_file), single_at_once: String(got.single_at_once) }
}, { immediate: true })
// A field writes only itself: the other limits are the core's until their own fields are changed.
async function saveLimits(field: keyof typeof limits.value) {
  await saveShared({ [field]: limits.value[field].trim() })
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
  for (const flag of FLAGS) flags[flag.key] = await loadFlag(flag.path)
  void loadLimits()
  sourcesTerm.value = await loadNum("/api/settings/sources_term", 1)
})
</script>

<template>
  <div class="sheet settings-debug">
    <!-- Сканер -->
    <SheetRow :caption="t('debug.sync.title')">
      <div class="sheet-pair">
        <SheetField :label="t('series.autoSync')" control>
          <StSwitch :model-value="enabled" @update:model-value="setEnabled" />
        </SheetField>
        <SheetField :label="t('debug.autoSync.interval')" control>
          <StNumber v-model="interval" @change="saveScanner" />
        </SheetField>
      </div>
      <div class="sheet-line">
        <Button
          :label="t('debug.syncAll.button')"
          icon="pi pi-refresh"
          :disabled="scanner.status?.is_scanning || scanner.status?.is_awaiting_tasks"
          @click="scanAll"
        />
      </div>
    </SheetRow>

    <!-- Параметры конвейера и отладка -->
    <SheetRow :caption="t('debug.pipeline.title')">
      <div class="sheet-pair">
        <SheetField v-for="flag in FLAGS" :key="flag.key" :label="t(flag.label)" :title="t(flag.hint)" control>
          <StSwitch :model-value="flags[flag.key]" @update:model-value="setFlag(flag, $event)" />
        </SheetField>
      </div>
      <div class="sheet-pair">
        <SheetField v-for="each in LIMITS" :key="each.field" :label="t(each.label)" :title="each.hint ? t(each.hint) : undefined" control>
          <StNumber v-model="limits[each.field]" @change="saveLimits(each.field)" />
        </SheetField>
        <SheetField :label="t('debug.sourcesTerm.label')" :title="t('debug.sourcesTerm.hint')" control>
          <StNumber v-model="sourcesTerm" @change="saveSourcesTerm" />
        </SheetField>
      </div>
      <p class="sheet-hint">{{ t("debug.downloads.multiAtOnce", { count: multiAtOnce }) }}</p>
    </SheetRow>

    <!-- База данных -->
    <SheetRow :caption="t('debug.db.title')">
      <div class="sheet-line">
        <Button :label="t('debug.db.view')" icon="pi pi-table" severity="info" @click="showDb = true" />
        <span class="sheet-hint">{{ t("debug.db.viewHint") }}</span>
      </div>
      <p class="sheet-hint is-danger">{{ t("debug.db.clearWarning") }}</p>
      <SheetField :label="t('debug.db.clear')" icon="pi pi-trash">
        <StSelect v-model="selectedTable" :options="tableOptions" :placeholder="t('debug.db.chooseTable')" />
        <div class="constructor-item item-label clear-desc">{{ tableDescription }}</div>
        <div class="constructor-item item-button-group">
          <button class="btn-icon btn-delete" :title="t('debug.db.clear')" :disabled="!selectedTable" @click="clearTable">
            <i class="pi pi-trash" />
          </button>
        </div>
      </SheetField>
    </SheetRow>

    <!-- Сохранённые пути -->
    <SheetRow :caption="t('savedPaths.title')" :count="paths.length">
      <div v-if="paths.length" class="saved-paths-list">
        <div v-for="p in paths" :key="p.id" class="saved-path-row">
          <span class="saved-path-value">{{ p.path }}</span>
          <Button icon="pi pi-times" severity="danger" text rounded :title="t('common.delete')" @click="removePath(p.id)" />
        </div>
      </div>
      <p v-else class="sheet-hint">{{ t("debug.paths.empty") }}</p>
      <SheetField :label="t('debug.paths.new')" icon="pi pi-folder">
        <StInput v-model="newPath" placeholder="/nas/media/..." @keyup.enter="addPath" />
        <div class="constructor-item item-button-group">
          <StBtn icon="pi pi-plus" variant="add" :disabled="!newPath.trim()" :title="t('common.add')" :aria-label="t('common.add')" @click="addPath" />
        </div>
      </SheetField>
    </SheetRow>

    <DatabaseViewerModal v-if="showDb" @close="showDb = false" />
  </div>
</template>

<style scoped>
.clear-desc { flex-grow: 1; justify-content: flex-start; white-space: normal; color: var(--text-muted); font-weight: 400; }
.saved-paths-list { display: flex; flex-direction: column; gap: 0.4rem; }
.saved-path-row {
  display: flex; align-items: center; justify-content: space-between; gap: 0.75rem;
  padding: 0.25rem 0.4rem 0.25rem 0.9rem; background: var(--bg-light); border-radius: var(--border-radius);
}
.saved-path-value { font-family: var(--font-mono); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
</style>
