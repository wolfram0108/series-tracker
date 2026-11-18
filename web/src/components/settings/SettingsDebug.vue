<script setup lang="ts">
import { ref, computed, onMounted, watch } from "vue"
import Button from "primevue/button"
import { useToast } from "primevue/usetoast"
import SheetField from "../SheetField.vue"
import SheetRow from "../SheetRow.vue"
import StBtn from "../StBtn.vue"
import StInput from "../StInput.vue"
import StNumber from "../StNumber.vue"
import StSelect from "../StSelect.vue"
import DatabaseViewerModal from "./DatabaseViewerModal.vue"
import ScheduleSettings from "./ScheduleSettings.vue"
import { api } from "../../api/client"
import { sanitizePath } from "../../lib/fsnames"
import { t, type MessageKey } from "../../i18n"
import { useApi } from "../../composables/useApi"
import { useDownloadLimits } from "../../composables/useDownloadLimits"
import { useConfirm } from "../../composables/useConfirm"
import { reasonText } from "../../lib/reason"
import LoadGate from "../LoadGate.vue"
import { useLoad } from "../../composables/useLoad"

// Вкладка «Отладка»: сканер, сохранённые пути, флаги/параметры конвейера,
// просмотр и очистка БД. Контракт проверен по modules/gateway/api_settings.py.
const { request } = useApi()
const confirm = useConfirm()
const toast = useToast()

// --- the limits of the downloads by way (a state shared with the window of the extensions: composables/useDownloadLimits.ts) ----
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
    api.POST("/api/settings/saved_paths", { body: { path: v } }),
    { errorMessage: t("debug.paths.addFailed") },
  )
  if (ok !== null) {
    newPath.value = ""
    await loadPaths()
  }
}
async function removePath(id: number) {
  const ok = await request(
    api.DELETE("/api/settings/saved_paths", { body: { id } }),
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
    api.POST("/api/database/clear_table", { body: { table_name: selectedTable.value } }),
    { errorMessage: t("debug.db.clear.failed") },
  )) as { success?: boolean; message?: string } | null
  if (data?.success) {
    toast.add({ severity: "success", summary: t("debug.db.toast"), detail: reasonText(data.message) || t("debug.db.clear.done"), life: 3000 })
  }
}

// Everything the tab shows is read together; the form is put on show when it all has come (components/LoadGate.vue).
async function readAll(): Promise<boolean> {
  await Promise.all([loadPaths(), loadTables(), loadLimits()])
  return true
}
const gate = useLoad(readAll)
onMounted(gate.run)
</script>

<template>
  <LoadGate :state="gate.state.value" :rows="6" @retry="gate.run">
    <div class="sheet settings-debug">
      <!-- The schedule of the day: the syncs, the checks of the torrent releases, the terms of the sites -->
      <SheetRow :caption="t('schedule.title')">
        <ScheduleSettings />
      </SheetRow>

      <!-- The pipeline and debugging -->
      <SheetRow :caption="t('debug.pipeline.title')">
        <div class="sheet-pair">
          <SheetField v-for="each in LIMITS" :key="each.field" :label="t(each.label)" :title="each.hint ? t(each.hint) : undefined" control>
            <StNumber v-model="limits[each.field]" @change="saveLimits(each.field)" />
          </SheetField>
        </div>
        <p class="sheet-hint">{{ t("debug.downloads.multiAtOnce", { count: multiAtOnce }) }}</p>
      </SheetRow>

      <!-- Database -->
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

      <!-- Saved paths -->
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
  </LoadGate>
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
