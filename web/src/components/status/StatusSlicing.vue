<script setup lang="ts">
import { ref, onMounted } from "vue"
import { useToast } from "primevue/usetoast"
import Button from "primevue/button"
import { api } from "../../api/client"
import { useApi } from "../../composables/useApi"
import { useConfirm } from "../../composables/useConfirm"
import { t, tn } from "../../i18n"
import { stateLabel } from "../../lib/states"
import { reasonText } from "../../lib/reason"

// Вкладка «Нарезка» — порт ChapterManager. Компиляции в плане/обработанные
// слайсером, операции над главами: проверить оглавление (ffprobe), фильтр,
// ручной выбор, нарезка (с фильтрацией/без), удалить исходник.
const props = defineProps<{ seriesId: number; seriesName: string }>()
const { request } = useApi()
const toast = useToast()
const confirm = useConfirm()

interface Chapter { time?: string; title?: string; garbage_reason?: string; original_index?: number }
interface Item {
  unique_id: string
  episode_start?: number
  episode_end?: number
  status?: string
  plan_status?: string
  slicing_status?: string
  final_filename?: string | null
  source_url?: string
  chapters: Chapter[] | null
  filteredChapters: Chapter[] | null
  garbageChapters: Chapter[] | null
  selectedChapters: number[]
  showChapterSelection: boolean
  statusMessage: string | null
  isLoadingChapters: boolean
}

const loading = ref(false)
const items = ref<Item[]>([])

function baseName(p?: string | null): string {
  return p ? (p.split(/[\\/]/).pop() ?? "") : ""
}
function expectedCount(it: Item): number {
  return (it.episode_end ?? 0) - (it.episode_start ?? 0) + 1
}
function canSlice(it: Item): boolean {
  return !!it.chapters && it.chapters.length > 0
}
function canDeleteSource(it: Item): boolean {
  return ["completed", "completed_with_errors"].includes(it.slicing_status ?? "") && !!it.final_filename
}
function isSliceDisabled(it: Item): boolean {
  return !["none", "completed_with_errors", "error"].includes(it.slicing_status ?? "")
}
function sliceTitle(it: Item): string {
  const m: Record<string, string> = {
    none: t("slicing.start.series"),
    completed_with_errors: t("slicing.repair"),
    pending: stateLabel("queued"),
    slicing: stateLabel("slice"),
    completed: stateLabel("done"),
    error: t("slicing.retry"),
  }
  return m[it.slicing_status ?? ""] || t("slicing.start")
}
function cardClass(it: Item): string {
  if (it.chapters === null) return "status-warning"
  if (it.chapters.length === 0) return "status-danger"
  return it.chapters.length === expectedCount(it) ? "status-success" : "status-danger"
}
function statusText(it: Item): string {
  const found = it.chapters ? it.chapters.length : 0
  const exp = expectedCount(it)
  if (it.chapters === null) return t("slicing.status.unchecked")
  if (found === 0) return t("slicing.status.notFound", { found, exp })
  if (found === exp) return t("slicing.status.match", { found, exp })
  return t("slicing.status.mismatch", { found, exp })
}

async function loadMediaItems() {
  if (loading.value) return
  loading.value = true
  try {
    const raw = (await request(
      api.GET("/api/series/{series_id}/media-items", { params: { path: { series_id: props.seriesId } } } as never),
      { errorMessage: t("slicing.load.error") },
    )) as Array<Record<string, unknown>> | null
    if (!Array.isArray(raw)) return
    items.value = raw
      .filter((m) => {
        const isCompilation = !!m.episode_end && Number(m.episode_end) > Number(m.episode_start)
        const isDownloaded = m.status === "completed"
        const isInPlan = m.plan_status === "in_plan_compilation"
        const wasProcessed = m.slicing_status !== "none"
        return isCompilation && isDownloaded && (isInPlan || wasProcessed)
      })
      .map((m) => ({
        unique_id: String(m.unique_id),
        episode_start: m.episode_start as number,
        episode_end: m.episode_end as number,
        status: m.status as string,
        plan_status: m.plan_status as string,
        slicing_status: (m.slicing_status as string) ?? "none",
        final_filename: (m.final_filename as string) ?? null,
        source_url: m.source_url as string,
        chapters: m.chapters ? (JSON.parse(m.chapters as string) as Chapter[]) : null,
        filteredChapters: null,
        garbageChapters: null,
        selectedChapters: [],
        showChapterSelection: false,
        statusMessage: null,
        isLoadingChapters: false,
      }))
      .sort((a, b) => (a.episode_start ?? 0) - (b.episode_start ?? 0))
  } finally {
    loading.value = false
  }
}

async function fetchChapters(it: Item) {
  it.isLoadingChapters = true
  try {
    const data = (await request(
      api.POST("/api/media-items/{unique_id}/chapters", { params: { path: { unique_id: it.unique_id } } } as never),
      { errorMessage: t("slicing.chapters.error") },
    )) as Chapter[] | null
    if (Array.isArray(data)) {
      it.chapters = data
      it.filteredChapters = null
      it.garbageChapters = null
      it.statusMessage = null
    }
  } finally {
    it.isLoadingChapters = false
  }
}
async function fetchFilteredChapters(it: Item) {
  it.isLoadingChapters = true
  try {
    const data = (await request(
      api.POST("/api/media-items/{unique_id}/chapters/filtered", { params: { path: { unique_id: it.unique_id } } } as never),
      { errorMessage: t("slicing.filter.error") },
    )) as { chapters?: Chapter[]; filtered_chapters?: Chapter[]; garbage_chapters?: Chapter[]; status_message?: string } | null
    if (data) {
      it.chapters = data.chapters ?? it.chapters
      it.filteredChapters = data.filtered_chapters ?? null
      it.garbageChapters = data.garbage_chapters ?? null
      it.statusMessage = data.status_message ? reasonText(data.status_message) : null
      toast.add({ severity: "success", summary: t("history.col.chapters"), detail: t("slicing.toast.filtered"), life: 2500 })
    }
  } finally {
    it.isLoadingChapters = false
  }
}
function toggleSelection(it: Item) {
  it.showChapterSelection = !it.showChapterSelection
  if (it.showChapterSelection && it.chapters) {
    if (it.filteredChapters) {
      it.selectedChapters = it.filteredChapters.map((ch) =>
        it.chapters!.findIndex((c) => c.time === ch.time && c.title === ch.title),
      )
    } else {
      it.selectedChapters = it.chapters.map((_, i) => i)
    }
  }
}
async function applyManualFilter(it: Item) {
  if (!it.chapters) return
  const garbage = it.chapters.map((_, i) => i).filter((i) => !it.selectedChapters.includes(i))
  const data = (await request(
    api.POST("/api/media-items/{unique_id}/chapters/mark-garbage", {
      params: { path: { unique_id: it.unique_id } },
      body: { garbage_indices: garbage },
    } as never),
    { errorMessage: t("slicing.mark.error") },
  )) as { chapters?: Chapter[]; filtered_chapters?: Chapter[]; garbage_chapters?: Chapter[]; status_message?: string } | null
  if (data) {
    it.chapters = data.chapters ?? it.chapters
    it.filteredChapters = data.filtered_chapters ?? null
    it.garbageChapters = data.garbage_chapters ?? null
    it.statusMessage = data.status_message ? reasonText(data.status_message) : null
    it.showChapterSelection = false
    toast.add({ severity: "success", summary: t("history.col.chapters"), detail: t("slicing.toast.manualApplied"), life: 2500 })
  }
}
async function createSlicingTask(it: Item) {
  const r = await confirm.open({
    title: t("slicing.confirm.runTitle"),
    message: t("slicing.confirm.run", { name: baseName(it.final_filename) }),
  })
  if (!r.confirmed) return
  const prev = it.slicing_status
  it.slicing_status = "pending"
  const data = (await request(
    api.POST("/api/media-items/{unique_id}/slice", { params: { path: { unique_id: it.unique_id } } } as never),
    { errorMessage: t("slicing.task.error") },
  )) as { source_missing?: boolean; message?: string } | null
  if (data === null) {
    it.slicing_status = "error"
    return
  }
  if (data.source_missing) {
    it.slicing_status = prev
    toast.add({ severity: "warn", summary: t("status.tab.slicing"), detail: reasonText(data.message) || t("slicing.toast.sourceMissingDownload"), life: 4000 })
    return
  }
  toast.add({ severity: "success", summary: t("status.tab.slicing"), detail: t("slicing.toast.created"), life: 3000 })
}
async function createSlicingTaskWithFilter(it: Item) {
  const count = it.filteredChapters ? it.filteredChapters.length : it.chapters?.length ?? 0
  const r = await confirm.open({
    title: t("slicing.confirm.filterTitle"),
    message: tn("slicing.confirm.filter.count", count, { name: baseName(it.final_filename) }),
  })
  if (!r.confirmed) return
  const prev = it.slicing_status
  it.slicing_status = "pending"
  const garbage = it.garbageChapters ? it.garbageChapters.map((ch) => ch.original_index).filter((x): x is number => x != null) : []
  const data = (await request(
    api.POST("/api/media-items/{unique_id}/slice-with-filter", {
      params: { path: { unique_id: it.unique_id } },
      body: { garbage_indices: garbage },
    } as never),
    { errorMessage: t("slicing.task.error") },
  )) as { source_missing?: boolean; message?: string } | null
  if (data === null) {
    it.slicing_status = "error"
    return
  }
  if (data.source_missing) {
    it.slicing_status = prev
    toast.add({ severity: "warn", summary: t("status.tab.slicing"), detail: reasonText(data.message) || t("slicing.toast.sourceMissing"), life: 4000 })
    return
  }
  toast.add({ severity: "success", summary: t("status.tab.slicing"), detail: t("slicing.toast.createdFiltered"), life: 3000 })
}
async function deleteSource(it: Item) {
  const r = await confirm.open({
    title: t("slicing.confirm.deleteSourceTitle"),
    message: t("slicing.confirm.deleteSource", { name: baseName(it.final_filename) }),
  })
  if (!r.confirmed) return
  const data = (await request(
    api.POST("/api/media-items/{unique_id}/delete-source", { params: { path: { unique_id: it.unique_id } } } as never),
    { errorMessage: t("slicing.deleteSource.error") },
  )) as { message?: string } | null
  if (data !== null) {
    it.final_filename = null
    toast.add({ severity: "success", summary: t("slicing.toast.deleted"), detail: reasonText(data.message) || t("slicing.toast.sourceDeleted"), life: 3000 })
  }
}

onMounted(loadMediaItems)
</script>

<template>
  <div class="status-slicing">
    <div v-if="loading" class="status-loading"><i class="pi pi-spin pi-spinner" style="font-size: 1.6rem" /></div>
    <div v-else-if="!items.length" class="empty-state">{{ t("slicing.empty") }}</div>

    <div v-else class="compilation-cards-container">
      <div v-for="it in items" :key="it.unique_id" class="slicing-card slicing-card-accent" :class="cardClass(it)">
        <div class="slicing-card-header">
          <strong class="compilation-title" :title="it.final_filename || it.source_url">
            {{ baseName(it.final_filename) || t("slicing.compilation.title", { start: it.episode_start ?? "", end: it.episode_end ?? "" }) }}
          </strong>
          <div class="slicing-card-actions">
            <button class="control-btn" :disabled="it.isLoadingChapters" :title="t('slicing.check')" @click="fetchChapters(it)">
              <i class="pi" :class="it.isLoadingChapters ? 'pi-spin pi-spinner' : 'pi-search'" />
            </button>
            <button v-if="canSlice(it)" class="control-btn text-info" :disabled="it.isLoadingChapters" :title="t('slicing.checkFilter')" @click="fetchFilteredChapters(it)">
              <i class="pi" :class="it.isLoadingChapters ? 'pi-spin pi-spinner' : 'pi-filter'" />
            </button>
            <button v-if="canSlice(it)" class="control-btn text-primary" :disabled="isSliceDisabled(it)" :title="sliceTitle(it)" @click="createSlicingTask(it)">
              <i class="pi pi-images" />
            </button>
            <button v-if="canDeleteSource(it)" class="control-btn text-danger" :title="t('slicing.deleteSource')" @click="deleteSource(it)">
              <i class="pi pi-trash" />
            </button>
          </div>
        </div>

        <div v-if="it.chapters && it.chapters.length" class="slicing-card-body">
          <!-- активные главы -->
          <div v-if="it.filteredChapters && it.filteredChapters.length" class="chapter-section">
            <h6>{{ t("slicing.chapters.active", { count: it.filteredChapters.length }) }}</h6>
            <div class="chapter-list">
              <span v-for="(ch, i) in it.filteredChapters" :key="'f' + i" class="chapter-pill chapter-active">{{ ch.time }} ({{ ch.title }})</span>
            </div>
          </div>
          <!-- мусорные главы -->
          <div v-if="it.garbageChapters && it.garbageChapters.length" class="chapter-section">
            <h6>{{ t("slicing.chapters.garbage", { count: it.garbageChapters.length }) }}</h6>
            <div class="chapter-list">
              <span v-for="(ch, i) in it.garbageChapters" :key="'g' + i" class="chapter-pill chapter-garbage" :title="reasonText(ch.garbage_reason)">
                {{ ch.time }} ({{ ch.title }}) <i class="pi pi-times-circle" />
              </span>
            </div>
          </div>
          <!-- все главы без фильтрации -->
          <div v-if="!it.filteredChapters && !it.garbageChapters" class="chapter-list">
            <span v-for="(ch, i) in it.chapters" :key="i" class="chapter-pill">{{ ch.time }} ({{ ch.title }})</span>
          </div>

          <!-- управление фильтрацией -->
          <div v-if="it.garbageChapters && it.garbageChapters.length" class="chapter-controls">
            <Button :label="t('slicing.chapters.pick')" icon="pi pi-check-square" size="small" severity="secondary" outlined @click="toggleSelection(it)" />
            <Button :label="t('slicing.filterSlice')" icon="pi pi-images" size="small" outlined @click="createSlicingTaskWithFilter(it)" />
          </div>

          <!-- ручной выбор глав -->
          <div v-if="it.showChapterSelection" class="chapter-selection">
            <h6>{{ t("slicing.chapters.manualTitle") }}</h6>
            <div class="chapter-checkbox-list">
              <div v-for="(ch, i) in it.chapters" :key="'s' + i" class="chk-row">
                <input :id="`ch-${it.unique_id}-${i}`" v-model="it.selectedChapters" type="checkbox" :value="i" />
                <label :for="`ch-${it.unique_id}-${i}`">{{ ch.time }} — {{ ch.title }}</label>
              </div>
            </div>
            <div class="chapter-selection-actions">
              <Button :label="t('slicing.applyFilter')" size="small" @click="applyManualFilter(it)" />
              <Button :label="t('common.cancel')" size="small" severity="secondary" @click="toggleSelection(it)" />
            </div>
          </div>
        </div>

        <div class="slicing-card-footer card-footer-status">
          <span>{{ statusText(it) }}</span>
          <div v-if="it.statusMessage" class="status-message"><small>{{ it.statusMessage }}</small></div>
        </div>
      </div>
    </div>
  </div>
</template>
