<script setup lang="ts">
import { ref, onMounted } from "vue"
import { useToast } from "primevue/usetoast"
import Button from "primevue/button"
import { api } from "../../api/client"
import { useApi } from "../../composables/useApi"
import { useConfirm } from "../../composables/useConfirm"

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
    none: "Начать нарезку на эпизоды",
    completed_with_errors: "Восстановить недостающие файлы",
    pending: "В очереди на нарезку",
    slicing: "В процессе нарезки…",
    completed: "Нарезка успешно завершена",
    error: "Произошла ошибка. Попробовать снова?",
  }
  return m[it.slicing_status ?? ""] || "Начать нарезку"
}
function cardClass(it: Item): string {
  if (it.chapters === null) return "status-warning"
  if (it.chapters.length === 0) return "status-danger"
  return it.chapters.length === expectedCount(it) ? "status-success" : "status-danger"
}
function statusText(it: Item): string {
  const found = it.chapters ? it.chapters.length : 0
  const exp = expectedCount(it)
  if (it.chapters === null) return "Не проверено"
  if (found === 0) return `${found} из ${exp} глав (Оглавление не найдено)`
  if (found === exp) return `${found} из ${exp} глав (Соответствует)`
  return `${found} из ${exp} глав (Несоответствие)`
}

async function loadMediaItems() {
  if (loading.value) return
  loading.value = true
  try {
    const raw = (await request(
      api.GET("/api/series/{series_id}/media-items", { params: { path: { series_id: props.seriesId } } } as never),
      { errorMessage: "Ошибка загрузки медиа-элементов" },
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
      { errorMessage: "Ошибка получения глав" },
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
      { errorMessage: "Ошибка фильтрации глав" },
    )) as { chapters?: Chapter[]; filtered_chapters?: Chapter[]; garbage_chapters?: Chapter[]; status_message?: string } | null
    if (data) {
      it.chapters = data.chapters ?? it.chapters
      it.filteredChapters = data.filtered_chapters ?? null
      it.garbageChapters = data.garbage_chapters ?? null
      it.statusMessage = data.status_message ?? null
      toast.add({ severity: "success", summary: "Главы", detail: "Отфильтрованы", life: 2500 })
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
    { errorMessage: "Ошибка разметки глав" },
  )) as { chapters?: Chapter[]; filtered_chapters?: Chapter[]; garbage_chapters?: Chapter[]; status_message?: string } | null
  if (data) {
    it.chapters = data.chapters ?? it.chapters
    it.filteredChapters = data.filtered_chapters ?? null
    it.garbageChapters = data.garbage_chapters ?? null
    it.statusMessage = data.status_message ?? null
    it.showChapterSelection = false
    toast.add({ severity: "success", summary: "Главы", detail: "Ручная разметка применена", life: 2500 })
  }
}
async function createSlicingTask(it: Item) {
  const r = await confirm.open({
    title: "Запуск нарезки",
    message: `Запустить нарезку файла «${baseName(it.final_filename)}»?`,
  })
  if (!r.confirmed) return
  const prev = it.slicing_status
  it.slicing_status = "pending"
  const data = (await request(
    api.POST("/api/media-items/{unique_id}/slice", { params: { path: { unique_id: it.unique_id } } } as never),
    { errorMessage: "Ошибка создания задачи" },
  )) as { source_missing?: boolean; message?: string } | null
  if (data === null) {
    it.slicing_status = "error"
    return
  }
  if (data.source_missing) {
    it.slicing_status = prev
    toast.add({ severity: "warn", summary: "Нарезка", detail: data.message ?? "Исходник отсутствует — отправлен в дозагрузку", life: 4000 })
    return
  }
  toast.add({ severity: "success", summary: "Нарезка", detail: "Задача создана", life: 3000 })
}
async function createSlicingTaskWithFilter(it: Item) {
  const count = it.filteredChapters ? it.filteredChapters.length : it.chapters?.length ?? 0
  const r = await confirm.open({
    title: "Нарезка с фильтрацией",
    message: `Запустить нарезку файла «${baseName(it.final_filename)}»? Будет создано ${count} эпизодов.`,
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
    { errorMessage: "Ошибка создания задачи" },
  )) as { source_missing?: boolean; message?: string } | null
  if (data === null) {
    it.slicing_status = "error"
    return
  }
  if (data.source_missing) {
    it.slicing_status = prev
    toast.add({ severity: "warn", summary: "Нарезка", detail: data.message ?? "Исходник отсутствует", life: 4000 })
    return
  }
  toast.add({ severity: "success", summary: "Нарезка", detail: "Задача с фильтрацией создана", life: 3000 })
}
async function deleteSource(it: Item) {
  const r = await confirm.open({
    title: "Удаление исходного файла",
    message: `Удалить исходный файл компиляции «${baseName(it.final_filename)}»? Нарезанные эпизоды останутся.`,
  })
  if (!r.confirmed) return
  const data = (await request(
    api.POST("/api/media-items/{unique_id}/delete-source", { params: { path: { unique_id: it.unique_id } } } as never),
    { errorMessage: "Ошибка удаления исходника" },
  )) as { message?: string } | null
  if (data !== null) {
    it.final_filename = null
    toast.add({ severity: "success", summary: "Удалено", detail: data.message ?? "Исходный файл удалён", life: 3000 })
  }
}

onMounted(loadMediaItems)
</script>

<template>
  <div class="status-slicing">
    <div v-if="loading" class="status-loading"><i class="pi pi-spin pi-spinner" style="font-size: 1.6rem" /></div>
    <div v-else-if="!items.length" class="empty-state">В плане загрузки для этого сериала нет видео-компиляций.</div>

    <div v-else class="compilation-cards-container">
      <div v-for="it in items" :key="it.unique_id" class="slicing-card slicing-card-accent" :class="cardClass(it)">
        <div class="slicing-card-header">
          <strong class="compilation-title" :title="it.final_filename || it.source_url">
            {{ baseName(it.final_filename) || `Компиляция ${it.episode_start}-${it.episode_end}` }}
          </strong>
          <div class="slicing-card-actions">
            <button class="control-btn" :disabled="it.isLoadingChapters" title="Проверить оглавление" @click="fetchChapters(it)">
              <i class="pi" :class="it.isLoadingChapters ? 'pi-spin pi-spinner' : 'pi-search'" />
            </button>
            <button v-if="canSlice(it)" class="control-btn text-info" :disabled="it.isLoadingChapters" title="Проверить и отфильтровать" @click="fetchFilteredChapters(it)">
              <i class="pi" :class="it.isLoadingChapters ? 'pi-spin pi-spinner' : 'pi-filter'" />
            </button>
            <button v-if="canSlice(it)" class="control-btn text-primary" :disabled="isSliceDisabled(it)" :title="sliceTitle(it)" @click="createSlicingTask(it)">
              <i class="pi pi-images" />
            </button>
            <button v-if="canDeleteSource(it)" class="control-btn text-danger" title="Удалить исходный файл" @click="deleteSource(it)">
              <i class="pi pi-trash" />
            </button>
          </div>
        </div>

        <div v-if="it.chapters && it.chapters.length" class="slicing-card-body">
          <!-- активные главы -->
          <div v-if="it.filteredChapters && it.filteredChapters.length" class="chapter-section">
            <h6>Активные главы ({{ it.filteredChapters.length }}):</h6>
            <div class="chapter-list">
              <span v-for="(ch, i) in it.filteredChapters" :key="'f' + i" class="chapter-pill chapter-active">{{ ch.time }} ({{ ch.title }})</span>
            </div>
          </div>
          <!-- мусорные главы -->
          <div v-if="it.garbageChapters && it.garbageChapters.length" class="chapter-section">
            <h6>Мусорные главы ({{ it.garbageChapters.length }}):</h6>
            <div class="chapter-list">
              <span v-for="(ch, i) in it.garbageChapters" :key="'g' + i" class="chapter-pill chapter-garbage" :title="ch.garbage_reason">
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
            <Button label="Выбрать главы вручную" icon="pi pi-check-square" size="small" severity="secondary" outlined @click="toggleSelection(it)" />
            <Button label="Нарезать с фильтрацией" icon="pi pi-images" size="small" outlined @click="createSlicingTaskWithFilter(it)" />
          </div>

          <!-- ручной выбор глав -->
          <div v-if="it.showChapterSelection" class="chapter-selection">
            <h6>Ручной выбор глав для нарезки:</h6>
            <div class="chapter-checkbox-list">
              <div v-for="(ch, i) in it.chapters" :key="'s' + i" class="chk-row">
                <input :id="`ch-${it.unique_id}-${i}`" v-model="it.selectedChapters" type="checkbox" :value="i" />
                <label :for="`ch-${it.unique_id}-${i}`">{{ ch.time }} — {{ ch.title }}</label>
              </div>
            </div>
            <div class="chapter-selection-actions">
              <Button label="Применить фильтр" size="small" @click="applyManualFilter(it)" />
              <Button label="Отмена" size="small" severity="secondary" @click="toggleSelection(it)" />
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
