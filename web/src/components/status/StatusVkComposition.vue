<script setup lang="ts">
import { ref, computed, onMounted } from "vue"
import { useToast } from "primevue/usetoast"
import Button from "primevue/button"
import ToggleSwitch from "primevue/toggleswitch"
import draggable from "vuedraggable"
import { api } from "../../api/client"
import { useApi } from "../../composables/useApi"
import { useConfirm } from "../../composables/useConfirm"

// Вкладка «Композиция» VK — порт seriesCompositionManager. Компиляции +
// нарезанные файлы + «дыры» (missing), сгруппированы по сезонам; приоритет
// качества (DnD), игнор сезонов/элементов, глубокое усыновление, переприменить
// правила, обновить с VK. См. порт-карту в snapshot.
const props = defineProps<{ seriesId: number; seriesName: string }>()
const { request } = useApi()
const toast = useToast()
const confirm = useConfirm()

interface SourceData { resolution?: number; url?: string; title?: string }
interface Extracted { season?: number; episode?: number; start?: number; end?: number; voiceover?: string }
interface MediaItem {
  unique_id: string
  season?: number
  source_data?: SourceData
  source_title?: string
  final_filename?: string
  status?: string
  plan_status?: string
  is_ignored_by_user?: boolean
  slicing_status?: string
  result?: { extracted?: Extracted }
}
interface SlicedFile {
  id: number
  source_media_item_unique_id?: string
  season?: number
  episode_number?: number
  parent_filename?: string
  file_path?: string
  parent_resolution?: number
  status?: string
}
interface Item {
  type: "compilation" | "sliced" | "missing"
  unique_id: string
  season?: number
  episode_start?: number
  source_data?: SourceData
  source_title?: string
  final_filename?: string
  status?: string
  plan_status?: string
  is_ignored_by_user?: boolean
  slicing_status?: string
  result?: { extracted?: Extracted }
  parent_filename?: string
  file_path?: string
  parent_resolution?: number
  episode_number?: number
  new_filename_preview?: string
}

const loading = ref(false)
const manualRefresh = ref(false)
const mediaItems = ref<MediaItem[]>([])
const slicedFiles = ref<SlicedFile[]>([])
const renamePreviews = ref<Record<string, string>>({})
const renameableCount = ref(0)
const reprocessing = ref(false)
const deepAdopting = ref(false)
const savingPriority = ref(false)
const showOnlyPlanned = ref(false)
const autoUpdate = ref(false)
const searchMode = ref("search")
const ignoredSeasons = ref<number[]>([])
const qualityPriority = ref<{ quality: number }[]>([])

// --- форматтеры ---
function baseName(p?: string): string {
  return p ? (p.split(/[\\/]/).pop() ?? "") : ""
}
function formatResolution(q: number): string {
  const map: Record<number, string> = { 2160: "4K 2160", 1440: "2K 1440", 1080: "FHD 1080", 720: "HD 720", 480: "SD 480", 360: "360", 240: "240" }
  return map[q] ?? `${q}`
}
function formatEpisode(it: Item): string {
  const ex = it.result?.extracted ?? {}
  const season = String(it.season ?? ex.season ?? 1).padStart(2, "0")
  const start = ex.start ?? ex.episode ?? it.episode_start
  const end = ex.end
  if (start && end && end !== start) return `s${season}e${String(start).padStart(2, "0")}-e${String(end).padStart(2, "0")}`
  if (start) return `s${season}e${String(start).padStart(2, "0")}`
  return `s${season}`
}
function voiceoverTag(it: Item): string {
  return it.result?.extracted?.voiceover || "N/A"
}
function formatVkUrl(url?: string): string {
  if (!url) return ""
  return url.split("/").pop() || url
}
async function copyUrl(url?: string) {
  if (!url) return
  try {
    await navigator.clipboard.writeText(url)
    toast.add({ severity: "success", summary: "Скопировано", detail: "Ссылка в буфере", life: 2000 })
  } catch {
    /* ignore */
  }
}
function episodeStart(it: { type: string; result?: { extracted?: Extracted }; episode_number?: number; episode_start?: number }): number {
  if (it.type === "compilation") return it.result?.extracted?.start ?? it.result?.extracted?.episode ?? 0
  if (it.type === "sliced") return it.episode_number ?? 0
  return it.episode_start ?? 0
}

// --- сборка единого списка (компиляции + нарезка + дыры) ---
const allItems = computed<Item[]>(() => {
  const items: Item[] = []
  for (const m of mediaItems.value) {
    items.push({ ...m, type: "compilation", new_filename_preview: renamePreviews.value[m.unique_id] })
  }
  for (const f of slicedFiles.value) {
    const uid = `sliced-${f.id}`
    const parent = mediaItems.value.find((m) => m.unique_id === f.source_media_item_unique_id)
    items.push({
      ...f,
      type: "sliced",
      unique_id: uid,
      season: f.season ?? parent?.season,
      parent_resolution: f.parent_resolution ?? parent?.source_data?.resolution,
      episode_start: f.episode_number,
      new_filename_preview: renamePreviews.value[uid],
    })
  }
  // дыры: по каждому сезону заполняем пропуски эпизодов (min..max)
  const bySeason = new Map<number, Set<number>>()
  for (const it of items) {
    const season = it.season ?? it.result?.extracted?.season
    if (season == null) continue
    const ep = episodeStart(it)
    if (!ep) continue
    if (!bySeason.has(season)) bySeason.set(season, new Set())
    bySeason.get(season)!.add(ep)
  }
  for (const [season, eps] of bySeason) {
    const nums = [...eps]
    const min = Math.min(...nums)
    const max = Math.max(...nums)
    for (let e = min; e <= max; e++) {
      if (!eps.has(e)) {
        items.push({ type: "missing", unique_id: `missing-s${season}-e${e}`, season, episode_start: e })
      }
    }
  }
  // сортировка по эпизоду DESC; при равенстве компиляция выше нарезки
  items.sort((a, b) => {
    const d = episodeStart(b) - episodeStart(a)
    if (d !== 0) return d
    const rank = (t: string) => (t === "compilation" ? 0 : t === "missing" ? 1 : 2)
    return rank(a.type) - rank(b.type)
  })
  return items
})

const groupedItems = computed(() => {
  const g: Record<string, Item[]> = {}
  for (const it of allItems.value) {
    const season = String(it.season ?? it.result?.extracted?.season ?? "undefined")
    ;(g[season] ??= []).push(it)
  }
  return g
})

function isItemInPlan(it: Item): boolean {
  if (it.is_ignored_by_user) return false
  const season = it.season ?? it.result?.extracted?.season
  if (season != null && ignoredSeasons.value.includes(season)) return false
  return it.plan_status === "in_plan_single" || it.plan_status === "in_plan_compilation"
}
const filteredGroupedItems = computed(() => {
  if (!showOnlyPlanned.value) return groupedItems.value
  const out: Record<string, Item[]> = {}
  for (const [season, items] of Object.entries(groupedItems.value)) {
    const keep = items.filter((it) => {
      if (it.type === "sliced") return true
      if (it.type === "compilation" && it.slicing_status === "completed" && it.is_ignored_by_user) return true
      return isItemInPlan(it)
    })
    if (keep.length) out[season] = keep
  }
  return out
})
const sortedSeasons = computed(() =>
  Object.keys(filteredGroupedItems.value).sort((a, b) => {
    if (a === "undefined") return 1
    if (b === "undefined") return -1
    return parseInt(a, 10) - parseInt(b, 10)
  }),
)

function seasonTitle(s: string): string {
  return s === "undefined" ? "Не определено" : `Сезон ${s.padStart(2, "0")}`
}
function isSeasonIgnored(s: string): boolean {
  return s !== "undefined" && ignoredSeasons.value.includes(parseInt(s, 10))
}

// статусный класс компиляции (порт getCardClass)
function cardClass(it: Item): string {
  if (it.slicing_status === "completed" && it.is_ignored_by_user) return "status-archived"
  if ((it.status || "").includes("error") || (it.slicing_status || "").includes("error")) return "status-pending"
  const season = it.season ?? it.result?.extracted?.season
  if (it.is_ignored_by_user || (season != null && ignoredSeasons.value.includes(season))) return "status-no-match"
  if (isItemInPlan(it) && it.status === "completed") return "status-success"
  if (isItemInPlan(it)) return "status-pending"
  return "status-no-match"
}

// --- загрузка ---
async function loadComposition(forceRefresh: boolean) {
  loading.value = true
  try {
    const comp = (await request(
      api.GET("/api/series/{series_id}/composition", {
        params: { path: { series_id: props.seriesId }, query: { refresh: forceRefresh } },
      } as never),
    )) as MediaItem[] | { mediaItems?: MediaItem[] } | null
    mediaItems.value = Array.isArray(comp) ? comp : (comp?.mediaItems ?? [])

    const prev = (await request(
      api.GET("/api/series/{series_id}/rename_preview", { params: { path: { series_id: props.seriesId } } } as never),
    )) as { preview?: { unique_id: string; new_filename_preview?: string }[]; needs_rename_count?: number } | null
    const map: Record<string, string> = {}
    for (const p of prev?.preview ?? []) if (p.new_filename_preview) map[p.unique_id] = p.new_filename_preview
    renamePreviews.value = map
    renameableCount.value = prev?.needs_rename_count ?? 0

    const series = (await request(
      api.GET("/api/series/{series_id}", { params: { path: { series_id: props.seriesId } } } as never),
    )) as Record<string, unknown> | null
    initQualityPriority(series)
    await loadSliced()
  } finally {
    loading.value = false
    manualRefresh.value = false
  }
}

async function loadSliced() {
  const sliced = (await request(
    api.GET("/api/series/{series_id}/sliced-files", { params: { path: { series_id: props.seriesId } } } as never),
  )) as SlicedFile[] | null
  if (Array.isArray(sliced)) slicedFiles.value = sliced
}

function initQualityPriority(series: Record<string, unknown> | null) {
  const present = new Set<number>()
  for (const m of mediaItems.value) if (m.source_data?.resolution) present.add(m.source_data.resolution)
  let saved: number[] = []
  const raw = series?.vk_quality_priority
  if (typeof raw === "string" && raw) {
    try {
      const parsed = JSON.parse(raw) as ({ quality: number } | number)[]
      saved = parsed.map((x) => (typeof x === "number" ? x : x.quality))
    } catch {
      saved = []
    }
  }
  const order: number[] = []
  for (const q of saved) if (present.has(q) && !order.includes(q)) order.push(q)
  for (const q of [...present].sort((a, b) => b - a)) if (!order.includes(q)) order.push(q)
  qualityPriority.value = order.map((q) => ({ quality: q }))
}

async function initialize() {
  const series = (await request(
    api.GET("/api/series/{series_id}", { params: { path: { series_id: props.seriesId } } } as never),
  )) as Record<string, unknown> | null
  searchMode.value = String(series?.vk_search_mode ?? "search")
  const ign = series?.ignored_seasons
  ignoredSeasons.value = Array.isArray(ign) ? (ign as number[]) : []
  // дефолт авто-обновления = true (как в оригинале: при открытии VK-серии
  // автоматически скрейпим/усыновляем). false только для get_all или если
  // пользователь явно выключил (сохранено в localStorage).
  if (searchMode.value === "get_all") {
    autoUpdate.value = false
  } else {
    const saved = localStorage.getItem(`composition_autoupdate_${props.seriesId}`)
    autoUpdate.value = saved !== null ? saved === "1" : true
  }
  await loadComposition(autoUpdate.value)
}

// --- действия ---
async function manualRefreshClick() {
  manualRefresh.value = true
  await loadComposition(true)
}
function onAutoUpdateChange() {
  localStorage.setItem(`composition_autoupdate_${props.seriesId}`, autoUpdate.value ? "1" : "0")
}
async function saveQualityPriority() {
  savingPriority.value = true
  try {
    const ok = await request(
      api.PUT("/api/series/{series_id}/vk-quality-priority", {
        params: { path: { series_id: props.seriesId } },
        body: { priority: qualityPriority.value },
      } as never),
      { errorMessage: "Ошибка сохранения приоритета" },
    )
    if (ok !== null) {
      toast.add({ severity: "success", summary: "Сохранено", detail: "Приоритет качества", life: 2500 })
      await loadComposition(false)
    }
  } finally {
    savingPriority.value = false
  }
}
async function toggleItemIgnored(it: Item) {
  const next = !it.is_ignored_by_user
  it.is_ignored_by_user = next // оптимистично
  const ok = await request(
    api.PUT("/api/media-items/{unique_id}/ignore", { params: { path: { unique_id: it.unique_id } }, body: { is_ignored: next } } as never),
    { errorMessage: "Ошибка изменения плана" },
  )
  if (ok === null) it.is_ignored_by_user = !next // откат
}
async function toggleSeasonIgnored(s: string) {
  const n = parseInt(s, 10)
  const was = ignoredSeasons.value.includes(n)
  ignoredSeasons.value = was ? ignoredSeasons.value.filter((x) => x !== n) : [...ignoredSeasons.value, n]
  const ok = await request(
    api.POST("/api/series/{series_id}/ignored-seasons", { params: { path: { series_id: props.seriesId } }, body: { seasons: ignoredSeasons.value } } as never),
    { errorMessage: "Ошибка игнорирования сезона" },
  )
  if (ok === null) ignoredSeasons.value = was ? [...ignoredSeasons.value, n] : ignoredSeasons.value.filter((x) => x !== n)
}
async function reprocessVk() {
  reprocessing.value = true
  try {
    const ok = await request(
      api.POST("/api/series/{series_id}/reprocess_vk_files", { params: { path: { series_id: props.seriesId } } } as never),
      { errorMessage: "Ошибка переобработки" },
    )
    if (ok !== null) {
      toast.add({ severity: "success", summary: "Переобработка", detail: "Задача принята", life: 3000 })
      setTimeout(() => loadComposition(false), 2000)
    }
  } finally {
    reprocessing.value = false
  }
}
async function deepAdoption() {
  const r = await confirm.open({
    title: "Глубокое усыновление",
    message: "Проверить все компиляции на диске и привязать найденные файлы? Операция может занять время.",
  })
  if (!r.confirmed) return
  deepAdopting.value = true
  try {
    const ok = await request(
      api.POST("/api/series/{series_id}/deep-adoption", { params: { path: { series_id: props.seriesId } } } as never),
      { errorMessage: "Ошибка усыновления" },
    )
    if (ok !== null) {
      toast.add({ severity: "success", summary: "Усыновление", detail: "Запущено", life: 3000 })
      setTimeout(() => loadComposition(false), 5000)
    }
  } finally {
    deepAdopting.value = false
  }
}

onMounted(initialize)

// Перечитывание по запросу модалки (переход на вкладку «Композиция»): после
// «Сохранить» фоновое усыновление меняет БД. Читаем из БД (refresh=false) —
// без повторного скрейпа VK (он только по кнопке «Обновить с VK»).
defineExpose({ reload: () => loadComposition(false) })
</script>

<template>
  <div class="status-vk-composition">
    <!-- Шапка управления -->
    <div class="modern-fieldset">
      <div class="fieldset-header">
        <span class="fieldset-title">Композиция</span>
        <div class="vk-comp-actions">
          <Button label="Глубокое усыновление" icon="pi pi-sparkles" size="small" severity="info" :loading="deepAdopting" :disabled="loading" @click="deepAdoption" />
          <Button label="Переприменить правила" icon="pi pi-pencil" size="small" severity="warn" :loading="reprocessing" :disabled="loading || !renameableCount" @click="reprocessVk" />
          <Button label="Обновить с VK" icon="pi pi-refresh" size="small" :loading="loading && manualRefresh" :disabled="loading" @click="manualRefreshClick" />
        </div>
      </div>
      <div class="fieldset-content">
        <div class="vk-comp-controls">
          <label class="vk-toggle">
            <ToggleSwitch v-model="autoUpdate" :disabled="searchMode === 'get_all'" @change="onAutoUpdateChange" />
            <span>Авто-обновление</span>
          </label>
          <label class="vk-toggle">
            <ToggleSwitch v-model="showOnlyPlanned" />
            <span>Только запланированные</span>
          </label>
        </div>

        <!-- Приоритет качества (DnD) -->
        <div v-if="qualityPriority.length" class="quality-settings-block">
          <div class="quality-settings-head">
            <span>Приоритет качества (перетащите)</span>
            <Button label="Сохранить приоритет" icon="pi pi-check" size="small" :loading="savingPriority" @click="saveQualityPriority" />
          </div>
          <draggable v-model="qualityPriority" item-key="quality" ghost-class="ghost-pill" :animation="200" class="quality-priority-list">
            <template #item="{ element }">
              <div class="quality-pill"><i class="pi pi-bars" /> {{ formatResolution(element.quality) }}</div>
            </template>
          </draggable>
        </div>
      </div>
    </div>

    <!-- Контент -->
    <div v-if="loading && !manualRefresh" class="status-loading"><i class="pi pi-spin pi-spinner" style="font-size: 1.6rem" /></div>
    <div v-else-if="!mediaItems.length && !slicedFiles.length" class="empty-state">Нет данных для отображения.</div>
    <div v-else>
      <div v-for="s in sortedSeasons" :key="s" class="season-group">
        <div class="season-header">
          <span>{{ seasonTitle(s) }}</span>
          <label v-if="s !== 'undefined'" class="season-ignore">
            <ToggleSwitch :model-value="!isSeasonIgnored(s)" @update:model-value="toggleSeasonIgnored(s)" />
            <span class="season-ignore-label">{{ isSeasonIgnored(s) ? "Игнор" : "В плане" }}</span>
          </label>
        </div>
        <div class="composition-cards-container">
          <template v-for="it in filteredGroupedItems[s]" :key="it.unique_id">
            <!-- компиляция -->
            <div v-if="it.type === 'compilation'" class="card-final card-compilation" :class="cardClass(it)">
              <div class="info-column">
                <div class="card-title-block">
                  <span class="card-title">{{ seriesName }} {{ formatEpisode(it) }}</span>
                  <div v-if="it.source_data?.resolution" class="quality-badge">{{ it.source_data.resolution }}p</div>
                </div>
                <div class="path-line">
                  <span class="path-pill"><span class="path-pill-label">Полученное:</span><span class="path-pill-value" :title="it.source_title">{{ it.source_title }}</span></span>
                </div>
                <div class="path-line">
                  <span class="path-pill" :class="{ 'is-missing': !it.final_filename }">
                    <span class="path-pill-label">Фактическое:</span>
                    <span class="path-pill-value" :title="it.final_filename">{{ it.final_filename ? baseName(it.final_filename) : "Файл не найден" }}</span>
                  </span>
                </div>
                <div v-if="it.new_filename_preview && it.new_filename_preview !== baseName(it.final_filename)" class="path-line">
                  <span class="path-pill is-mismatch"><span class="path-pill-label">Будет:</span><span class="path-pill-value">{{ it.new_filename_preview }}</span></span>
                </div>
              </div>
              <div class="pills-column">
                <div class="pill"><i class="pi pi-list-check" /><span>План: <strong>{{ it.plan_status || "—" }}</strong></span></div>
                <div class="pill"><i class="pi pi-tags" /><span>Тег: <strong>{{ voiceoverTag(it) }}</strong></span></div>
                <div class="pill"><i class="pi pi-check-square" /><span>Статус: <strong>{{ it.status || "—" }}</strong></span></div>
                <div class="pill"><i class="pi pi-images" /><span>Нарезка: <strong>{{ it.slicing_status || "—" }}</strong></span></div>
                <div class="pill"><i class="pi pi-id-card" /><span>ID: <strong>{{ it.unique_id.substring(0, 8) }}</strong></span></div>
                <div class="pill pill-link" @click="copyUrl(it.source_data?.url)"><i class="pi pi-link" /><span>{{ formatVkUrl(it.source_data?.url) }}</span></div>
              </div>
              <div class="controls-column">
                <ToggleSwitch
                  :model-value="!it.is_ignored_by_user"
                  :disabled="cardClass(it) === 'status-archived'"
                  @update:model-value="toggleItemIgnored(it)"
                />
              </div>
            </div>

            <!-- нарезанный файл -->
            <div v-else-if="it.type === 'sliced'" class="card-final card-sliced">
              <div class="info-column">
                <div class="card-title-block">
                  <span class="card-title">{{ seriesName }} {{ formatEpisode(it) }}</span>
                  <div v-if="it.parent_resolution" class="quality-badge">{{ it.parent_resolution }}p</div>
                </div>
                <div class="path-line">
                  <span class="path-pill"><span class="path-pill-label">Родитель:</span><span class="path-pill-value" :title="it.parent_filename">{{ baseName(it.parent_filename) }}</span></span>
                </div>
                <div class="path-line">
                  <span class="path-pill" :class="{ 'is-missing': it.status === 'missing' }"><span class="path-pill-label">Фактическое:</span><span class="path-pill-value" :title="it.file_path">{{ baseName(it.file_path) }}</span></span>
                </div>
                <div v-if="it.new_filename_preview" class="path-line">
                  <span class="path-pill is-mismatch"><span class="path-pill-label">Будет:</span><span class="path-pill-value">{{ it.new_filename_preview }}</span></span>
                </div>
              </div>
              <div class="pills-column">
                <div class="pill"><i class="pi pi-images" /><span>Нарезанный файл</span></div>
                <div class="pill" :class="{ 'pill-danger': it.status === 'missing' }"><span>{{ it.status === "missing" ? "Файл отсутствует" : "Файл на месте" }}</span></div>
              </div>
            </div>

            <!-- отсутствующий эпизод -->
            <div v-else class="card-final card-missing">
              <span class="card-title">Эпизод s{{ String(it.season).padStart(2, "0") }}e{{ String(it.episode_start).padStart(2, "0") }} — не найден в источнике</span>
              <i class="pi pi-eye-slash missing-icon" />
            </div>
          </template>
        </div>
      </div>
    </div>
  </div>
</template>
