<script setup lang="ts">
import { ref, reactive, computed, watch, onMounted, onBeforeUnmount } from "vue"
import Button from "primevue/button"
import { useToast } from "primevue/usetoast"
import ModalShell from "./ModalShell.vue"
import StGroup from "./StGroup.vue"
import StIcon from "./StIcon.vue"
import StInput from "./StInput.vue"
import StSelect from "./StSelect.vue"
import SavedPathDropdown from "./SavedPathDropdown.vue"
import { api } from "../api/client"
import { useApi } from "../composables/useApi"

// Модалка «Добавить сериал» — полный порт legacy addSeriesModal.js.
// Поток: ввод URL → debounce-распознавание (vkvideo.ru → VK-режим без
// отдельного API, парсинг ссылки в JS; иначе торрент-режим, POST
// /api/parse_url) → инфо о сериале → выбор качества по трекеру → опц.
// синхронизация с TMDB → сохранение POST /api/series.
const emit = defineEmits<{ (e: "close"): void; (e: "created"): void }>()
const { request } = useApi()
const toast = useToast()

// --- типы ответов (бэк отдаёт DynamicObject; сужаем под контракт) ---
interface Torrent { torrent_id?: string | number; link?: string; date_time?: string; episodes?: string; quality?: string }
interface ParseResult { success?: boolean; title?: { ru?: string; en?: string }; torrents?: Torrent[]; tracker_info?: TrackerInfo; error?: string }
interface TrackerInfo { ui_features?: { quality_selector?: string } }
interface TmdbResult { id: number; name?: string; original_name?: string; year?: string; poster_path?: string }
interface TmdbSeason { season_number: number; episode_count: number }

// --- состояние формы ---
const newSeries = reactive({
  url: "",
  save_path: "",
  name: "",
  name_en: "",
  season: "s01",
  qualityByEpisodes: {} as Record<string, string>,
  parser_profile_id: null as number | null,
  vk_search_mode: "search",
})
const isSeasonless = ref(false)
const urlError = ref("")
const parsing = ref(false)
const parsed = ref(false)
const site = ref("")
const parserData = ref<{ torrents: Torrent[] } | null>(null)
const episodeQualityOptions = reactive<Record<string, string[]>>({})
const isQualityOptionsReady = ref(false)
const showValidation = ref(false)
const sourceType = ref<"torrent" | "vk_video">("torrent")
const vkChannelUrl = ref("")
const vkQuery = ref("")
const trackerInfo = ref<TrackerInfo | null>(null)
const profiles = ref<{ id: number; name: string }[]>([])
const saving = ref(false)

// --- TMDB ---
const tmdbSearchQuery = ref("")
const tmdbResults = ref<TmdbResult[]>([])
const tmdbSelected = ref<TmdbResult | null>(null)
const tmdbLoading = ref(false)
const tmdbEpisodeCount = ref(0)
const tmdbSeasonDetails = ref<TmdbSeason[]>([])

// сортировка ключей групп эпизодов astar (порт sortEpisodeKeys из index.html)
function sortEpisodeKeys(keys: string[]): string[] {
  const firstNum = (k: string) => {
    const m = k.match(/\d+/)
    return m ? parseInt(m[0], 10) : Infinity
  }
  return [...keys].sort((a, b) => {
    const na = firstNum(a)
    const nb = firstNum(b)
    return na === nb ? a.localeCompare(b) : na - nb
  })
}

// --- computed ---
const shouldShowValidation = computed(() => {
  if (showValidation.value) return true
  if (sourceType.value === "vk_video") return true
  if (sourceType.value === "torrent" && parsed.value) return true
  return false
})
const isSeasonValid = computed(() => {
  if (isSeasonless.value || sourceType.value === "vk_video") return true
  return /^s\d{2}$/.test(newSeries.season.trim())
})
const canAddSeries = computed(() => {
  const base = !!newSeries.name && !!newSeries.name_en && !!newSeries.save_path
  if (sourceType.value === "vk_video") {
    const vkOk = !!vkChannelUrl.value && !!newSeries.parser_profile_id
    const searchOk = newSeries.vk_search_mode === "search" ? !!vkQuery.value : true
    return base && vkOk && searchOk
  }
  return parsed.value && base && isSeasonValid.value
})
const sortedQualityOptionsKeys = computed(() => {
  if (!site.value.includes("astar")) return []
  return sortEpisodeKeys(Object.keys(episodeQualityOptions))
})
const tmdbSeasonNumber = computed(() => {
  if (isSeasonless.value) return 1
  const m = newSeries.season.match(/^s(\d+)$/i)
  return m ? parseInt(m[1], 10) : 1
})
// «Имя (год) [tmdbid-XXXX]» (шаблон Jellyfin/Plex); год опускается, если его нет
const tmdbCatalogName = computed(() => {
  if (!tmdbSelected.value) return ""
  const name = (tmdbSelected.value.name || "").trim()
  if (!name) return ""
  const year = String(tmdbSelected.value.year || "").trim()
  const idPart = `[tmdbid-${tmdbSelected.value.id}]`
  return year ? `${name} (${year}) ${idPart}` : `${name} ${idPart}`
})

// --- состояния валидации для рамок групп (valid/invalid/null) ---
type VState = "valid" | "invalid" | null
function vstate(ok: boolean): VState {
  return ok ? "valid" : "invalid"
}
const urlState = computed<VState>(() => {
  if (!shouldShowValidation.value) return null
  if (urlError.value) return "invalid"
  return vstate(sourceType.value === "vk_video" ? !!vkChannelUrl.value : parsed.value)
})
const vkChannelState = computed<VState>(() => (shouldShowValidation.value ? vstate(!!vkChannelUrl.value) : null))
const vkQueryState = computed<VState>(() => {
  if (!shouldShowValidation.value || newSeries.vk_search_mode !== "search") return null
  return vstate(!!vkQuery.value)
})
const profileState = computed<VState>(() => (shouldShowValidation.value ? vstate(!!newSeries.parser_profile_id) : null))
const nameState = computed<VState>(() => (shouldShowValidation.value ? vstate(!!newSeries.name) : null))
const nameEnState = computed<VState>(() => (shouldShowValidation.value ? vstate(!!newSeries.name_en) : null))
const savePathState = computed<VState>(() => (shouldShowValidation.value ? vstate(!!newSeries.save_path) : null))
const seasonState = computed<VState>(() => {
  if (!shouldShowValidation.value || isSeasonless.value || sourceType.value === "vk_video") return null
  return vstate(isSeasonValid.value)
})

const profileOptions = computed(() => [
  { label: "Выберите профиль...", value: null as number | null },
  ...profiles.value.map((p) => ({ label: p.name, value: p.id as number | null })),
])
const qualityOptionsAnilibria = computed(() => {
  const all = episodeQualityOptions.all
  if (!all) return []
  return all.map((q) => ({ label: q, value: q }))
})
const showQualityBlock = computed(() => !!trackerInfo.value?.ui_features?.quality_selector)
const qualitySelector = computed(() => trackerInfo.value?.ui_features?.quality_selector || "")
const showSeasonlessSwitch = computed(
  () => sourceType.value === "vk_video" || (!!site.value && (site.value.includes("kinozal") || site.value.includes("rutracker"))),
)
const astarHasAlternatives = computed(() => Object.values(episodeQualityOptions).some((o) => o.length > 1))

// --- автоисправление обратного слэша в путях (порт autoCorrectSlash) ---
watch(() => newSeries.save_path, (v) => {
  if (v.includes("\\")) newSeries.save_path = v.replace(/\\/g, "/")
})
watch(vkChannelUrl, (v) => {
  if (v.includes("\\")) vkChannelUrl.value = v.replace(/\\/g, "/")
})

// --- распознавание URL (debounce 500мс) ---
let debounceTimer: ReturnType<typeof setTimeout> | null = null
watch(() => newSeries.url, () => {
  if (debounceTimer) clearTimeout(debounceTimer)
  debounceTimer = setTimeout(handleUrlInput, 500)
})

function handleUrlInput() {
  urlError.value = ""
  parsed.value = false
  if (!newSeries.url) {
    sourceType.value = "torrent"
    return
  }
  if (newSeries.url.includes("vkvideo.ru")) {
    sourceType.value = "vk_video"
    try {
      const u = new URL(newSeries.url)
      vkChannelUrl.value = `${u.protocol}//${u.hostname}${u.pathname}`
      vkQuery.value = u.searchParams.get("q") || ""
    } catch {
      urlError.value = "Некорректный URL для VK Video"
      vkChannelUrl.value = ""
      vkQuery.value = ""
    }
  } else {
    sourceType.value = "torrent"
    void parseTorrentUrl()
  }
}

async function parseTorrentUrl() {
  parsing.value = true
  try {
    site.value = new URL(newSeries.url).hostname.replace(/^www\./, "")
  } catch {
    urlError.value = "Некорректный URL"
    parsing.value = false
    return
  }
  try {
    const { data, error } = await api.POST("/api/parse_url", { body: { url: newSeries.url } } as never)
    const res = (data ?? null) as ParseResult | null
    if (error || !res) {
      const err = (error ?? null) as { error?: string } | null
      throw new Error(err?.error || "Ошибка парсинга URL")
    }
    trackerInfo.value = res.tracker_info ?? null
    newSeries.name = res.title?.ru || ""
    newSeries.name_en = res.title?.en || ""
    const torrents = res.torrents ?? []
    parserData.value = { torrents }
    // сброс предыдущих качеств
    Object.keys(episodeQualityOptions).forEach((k) => delete episodeQualityOptions[k])
    Object.keys(newSeries.qualityByEpisodes).forEach((k) => delete newSeries.qualityByEpisodes[k])

    if (site.value.includes("anilibria") || site.value.includes("aniliberty")) {
      episodeQualityOptions.all = [...new Set(torrents.filter((t) => t.quality).map((t) => t.quality as string))]
      newSeries.qualityByEpisodes.all = episodeQualityOptions.all[0] || ""
    } else if (site.value.includes("astar")) {
      const versions: Record<string, string[]> = {}
      torrents.forEach((t) => {
        if (t.episodes) {
          if (!versions[t.episodes]) versions[t.episodes] = []
          if (t.quality) versions[t.episodes].push(t.quality)
        }
      })
      Object.assign(episodeQualityOptions, versions)
      sortedQualityOptionsKeys.value.forEach((ep) => {
        const opts = episodeQualityOptions[ep]
        newSeries.qualityByEpisodes[ep] = opts.find((q) => q !== "old") || opts[0] || ""
      })
    } else if (site.value.includes("rutracker")) {
      episodeQualityOptions.all = [...new Set(torrents.filter((t) => t.quality).map((t) => t.quality as string))]
      if (episodeQualityOptions.all.length > 0) newSeries.qualityByEpisodes.all = episodeQualityOptions.all[0] || ""
    }
    isQualityOptionsReady.value = true
    parsed.value = true
    showValidation.value = true
  } catch (e) {
    urlError.value = e instanceof Error ? e.message : String(e)
  } finally {
    parsing.value = false
  }
}

// --- TMDB ---
async function searchTMDB() {
  if (!tmdbSearchQuery.value) tmdbSearchQuery.value = newSeries.name || newSeries.name_en
  if (!tmdbSearchQuery.value) return
  tmdbLoading.value = true
  tmdbResults.value = []
  try {
    const { data } = await api.POST("/api/tmdb/search", { body: { query: tmdbSearchQuery.value } } as never)
    const res = (data ?? null) as { success?: boolean; results?: TmdbResult[]; error?: string } | null
    if (res?.success) {
      tmdbResults.value = res.results ?? []
    } else {
      toast.add({ severity: "warn", summary: "TMDB", detail: `Ошибка поиска: ${res?.error ?? "неизвестно"}`, life: 4000 })
    }
  } finally {
    tmdbLoading.value = false
  }
}
async function selectTMDBSeries(s: TmdbResult) {
  tmdbSelected.value = s
  const { data } = await api.GET("/api/tmdb/details/{tmdb_id}", { params: { path: { tmdb_id: s.id } } })
  const res = (data ?? null) as { success?: boolean; seasons?: TmdbSeason[] } | null
  if (res?.success) {
    tmdbSeasonDetails.value = res.seasons ?? []
    updateTMDBEpisodeCount()
  }
}
function clearTMDBSelection() {
  tmdbSelected.value = null
  tmdbSeasonDetails.value = []
  tmdbEpisodeCount.value = 0
}
async function copyCatalogName() {
  if (!tmdbCatalogName.value) return
  try {
    await navigator.clipboard.writeText(tmdbCatalogName.value)
    toast.add({ severity: "success", summary: "Скопировано", detail: "Имя каталога в буфере", life: 2500 })
  } catch {
    toast.add({ severity: "error", summary: "Ошибка", detail: "Не удалось скопировать", life: 2500 })
  }
}
function updateTMDBEpisodeCount() {
  if (!tmdbSelected.value || !tmdbSeasonDetails.value.length) return
  const s = tmdbSeasonDetails.value.find((x) => x.season_number === tmdbSeasonNumber.value)
  tmdbEpisodeCount.value = s ? s.episode_count : 0
}
watch(() => newSeries.season, updateTMDBEpisodeCount)
watch(() => newSeries.name, (v) => {
  if (!tmdbSearchQuery.value && !tmdbSelected.value) tmdbSearchQuery.value = v
})

// --- сохранение ---
async function addSeries() {
  showValidation.value = true
  if (!canAddSeries.value) {
    toast.add({ severity: "error", summary: "Проверьте поля", detail: "Заполните все обязательные поля корректно.", life: 4000 })
    return
  }
  saving.value = true
  try {
    const payload: Record<string, unknown> = {
      ...newSeries,
      site: site.value,
      season: isSeasonless.value ? "" : newSeries.season,
    }
    if (tmdbSelected.value) {
      payload.tmdb_data = {
        tmdb_id: tmdbSelected.value.id,
        tmdb_season_number: tmdbSeasonNumber.value,
        total_episodes: tmdbEpisodeCount.value,
        poster_path: tmdbSelected.value.poster_path,
        series_name: tmdbSelected.value.name,
        year: tmdbSelected.value.year,
      }
    }
    if (sourceType.value === "vk_video") {
      payload.source_type = "vk_video"
      payload.url = `${vkChannelUrl.value}|${vkQuery.value}`
      payload.site = "vkvideo.ru"
    } else {
      payload.source_type = "torrent"
      let quality = ""
      if (site.value.includes("anilibria") || site.value.includes("aniliberty")) {
        quality = newSeries.qualityByEpisodes.all || ""
      } else if (site.value.includes("astar")) {
        const multi = sortedQualityOptionsKeys.value
          .filter((ep) => episodeQualityOptions[ep].length > 1)
          .map((ep) => newSeries.qualityByEpisodes[ep])
        const single = new Set(
          sortedQualityOptionsKeys.value
            .filter((ep) => episodeQualityOptions[ep].length === 1)
            .map((ep) => episodeQualityOptions[ep][0]),
        )
        quality = [...multi, ...Array.from(single)].join(";")
      } else if (site.value.includes("rutracker")) {
        quality = newSeries.qualityByEpisodes.all || ""
      }
      payload.quality = quality
      payload.torrents = parserData.value?.torrents ?? []
    }
    const ok = await request(api.POST("/api/series", { body: payload } as never), {
      errorMessage: "Ошибка добавления сериала",
    })
    if (ok !== null) {
      toast.add({ severity: "success", summary: "Готово", detail: "Сериал успешно добавлен", life: 3000 })
      emit("created")
      emit("close")
    }
  } finally {
    saving.value = false
  }
}

async function loadProfiles() {
  const { data } = await api.GET("/api/parser-profiles")
  if (Array.isArray(data)) profiles.value = data as { id: number; name: string }[]
}
onMounted(loadProfiles)
onBeforeUnmount(() => {
  if (debounceTimer) clearTimeout(debounceTimer)
})
</script>

<template>
  <ModalShell size="xl" title="Добавить новый сериал" @close="emit('close')">
    <!-- URL -->
    <div class="field-group add-url-row">
      <StGroup :state="urlState">
        <StIcon icon="pi pi-link" />
        <StInput v-model="newSeries.url" label="URL для парсинга" />
      </StGroup>
      <div v-if="urlError" class="add-error">{{ urlError }}</div>
      <div v-if="parsing" class="add-hint">Распознаём ссылку…</div>
    </div>

    <div v-if="parsed || sourceType === 'vk_video'">
      <!-- VK -->
      <div v-if="sourceType === 'vk_video'" class="modern-fieldset">
        <div class="fieldset-header"><span class="fieldset-title">Настройки для VK Video</span></div>
        <div class="fieldset-content">
          <label class="modern-label">Режим поиска</label>
          <div class="vk-mode-group">
            <button
              type="button"
              class="vk-mode-btn"
              :class="{ active: newSeries.vk_search_mode === 'search' }"
              @click="newSeries.vk_search_mode = 'search'"
            >
              <i class="pi pi-search" /> Быстрый поиск
            </button>
            <button
              type="button"
              class="vk-mode-btn"
              :class="{ active: newSeries.vk_search_mode === 'get_all' }"
              @click="newSeries.vk_search_mode = 'get_all'"
            >
              <i class="pi pi-list" /> Полное сканирование
            </button>
          </div>
          <small class="fieldset-hint">
            <b>Быстрый поиск:</b> использует API поиска VK. Быстро, но может пропустить некоторые видео.<br />
            <b>Полное сканирование:</b> загружает все видео канала, затем фильтрует. Медленнее, но надёжнее.
          </small>
          <div class="add-grid-2 mt-3">
            <StGroup :state="vkChannelState">
              <StIcon icon="pi pi-youtube" />
              <StInput v-model="vkChannelUrl" label="Ссылка на канал" />
            </StGroup>
            <StGroup :state="vkQueryState">
              <StIcon icon="pi pi-search" />
              <StInput v-model="vkQuery" label="Поисковые запросы (через /)" />
            </StGroup>
          </div>
        </div>
      </div>

      <!-- Информация о сериале -->
      <div class="modern-fieldset">
        <div class="fieldset-header"><span class="fieldset-title">Информация о сериале</span></div>
        <div class="fieldset-content">
          <div class="add-grid-2">
            <StGroup :state="nameState">
              <StIcon icon="pi pi-pencil" />
              <StInput v-model="newSeries.name" label="Название (RU)" />
            </StGroup>
            <StGroup :state="nameEnState">
              <StIcon icon="pi pi-language" />
              <StInput v-model="newSeries.name_en" label="Название (EN)" />
            </StGroup>
          </div>
          <div class="add-grid-2 mt-3">
            <StGroup :state="savePathState">
              <StIcon icon="pi pi-folder-open" />
              <StInput v-model="newSeries.save_path" label="Путь сохранения" />
              <SavedPathDropdown :catalog-name="tmdbCatalogName" @select="newSeries.save_path = $event" />
            </StGroup>
            <StGroup :state="seasonState" :class="{ 'is-disabled': isSeasonless }">
              <StIcon icon="pi pi-hashtag" />
              <StInput v-model="newSeries.season" label="Сезон (формат s01)" />
            </StGroup>
          </div>
          <div class="field-group mt-3">
            <StGroup :state="profileState">
              <div class="constructor-item item-label-icon item-label-text-icon" title="Профиль правил">
                <i class="pi pi-filter" /><span>Профиль правил</span>
              </div>
              <StSelect v-model="newSeries.parser_profile_id" :options="profileOptions" placeholder="Выберите профиль..." />
            </StGroup>
          </div>
        </div>
      </div>

      <!-- Несколько сезонов -->
      <div v-if="showSeasonlessSwitch" class="modern-fieldset">
        <div class="fieldset-content">
          <label class="modern-form-check">
            <input v-model="isSeasonless" type="checkbox" class="form-switch-input" />
            <span class="modern-form-check-label">Раздача содержит несколько сезонов (или сезон не важен)</span>
          </label>
        </div>
      </div>

      <!-- Качество -->
      <div v-if="showQualityBlock" class="modern-fieldset">
        <div class="fieldset-header"><span class="fieldset-title">Выбор качества</span></div>
        <div class="fieldset-content">
          <template v-if="qualitySelector === 'anilibria'">
            <div v-if="isQualityOptionsReady && qualityOptionsAnilibria.length" class="field-group">
              <StGroup>
                <div class="constructor-item item-label">Качество</div>
                <StSelect
                  :model-value="newSeries.qualityByEpisodes.all"
                  :options="qualityOptionsAnilibria"
                  @update:model-value="newSeries.qualityByEpisodes.all = $event as string"
                />
              </StGroup>
            </div>
            <div v-else-if="isQualityOptionsReady" class="add-error">Качества не найдены</div>
          </template>

          <template v-if="qualitySelector === 'astar' && isQualityOptionsReady">
            <p class="fieldset-hint mb-2">Для релизов Astar можно выбрать версию для каждой группы эпизодов.</p>
            <template v-for="ep in sortedQualityOptionsKeys" :key="ep">
              <div v-if="episodeQualityOptions[ep].length > 1" class="field-group mt-2">
                <StGroup>
                  <div class="constructor-item item-label">Эпизоды {{ ep }}</div>
                  <StSelect
                    :model-value="newSeries.qualityByEpisodes[ep]"
                    :options="episodeQualityOptions[ep].map((q) => ({ label: q, value: q }))"
                    @update:model-value="newSeries.qualityByEpisodes[ep] = $event as string"
                  />
                </StGroup>
              </div>
            </template>
            <div v-if="!astarHasAlternatives" class="fieldset-hint">Для данного релиза нет альтернативных версий.</div>
          </template>
        </div>
      </div>

      <!-- TMDB -->
      <div class="modern-fieldset">
        <div class="fieldset-header"><span class="fieldset-title">Синхронизация с TMDB</span></div>
        <div class="fieldset-content">
          <div class="input-group mb-3">
            <input
              v-model="tmdbSearchQuery"
              type="text"
              class="form-control"
              placeholder="Название сериала"
              @keyup.enter="searchTMDB"
            />
            <button type="button" class="input-group-btn" :disabled="tmdbLoading" @click="searchTMDB">
              <i class="pi" :class="tmdbLoading ? 'pi-spin pi-spinner' : 'pi-search'" /> Найти
            </button>
          </div>

          <div v-if="tmdbResults.length" class="tmdb-results">
            <button
              v-for="res in tmdbResults"
              :key="res.id"
              type="button"
              class="tmdb-result"
              :class="{ active: tmdbSelected && tmdbSelected.id === res.id }"
              @click="selectTMDBSeries(res)"
            >
              <div class="tmdb-result-top">
                <span class="tmdb-result-name">{{ res.name }} <small>({{ res.year }})</small></span>
                <small class="tmdb-result-id">ID: {{ res.id }}</small>
              </div>
              <small class="tmdb-result-orig">{{ res.original_name }}</small>
            </button>
          </div>

          <div v-if="tmdbSelected" class="tmdb-selected">
            <i class="pi pi-check-circle" />
            <div class="tmdb-selected-text">
              Выбран: <strong>{{ tmdbSelected.name }}</strong><br />
              <small>Сезон {{ tmdbSeasonNumber }}: {{ tmdbEpisodeCount }} эпизодов</small>
            </div>
            <button type="button" class="tmdb-clear" title="Сбросить" @click="clearTMDBSelection">
              <i class="pi pi-times" />
            </button>
          </div>

          <div v-if="tmdbSelected && tmdbCatalogName" class="tmdb-catalog">
            <label class="tmdb-catalog-label">Имя каталога</label>
            <div class="input-group">
              <input class="form-control" :value="tmdbCatalogName" readonly @focus="($event.target as HTMLInputElement).select()" />
              <button type="button" class="input-group-btn" title="Скопировать" @click="copyCatalogName">
                <i class="pi pi-clipboard" />
              </button>
            </div>
          </div>
        </div>
      </div>

      <!-- Доступные торренты -->
      <div v-if="sourceType === 'torrent' && parserData && parserData.torrents.length" class="add-torrents">
        <h6 class="add-torrents-title">Доступные торренты ({{ parserData.torrents.length }})</h6>
        <div class="div-table table-site-torrents">
          <div class="div-table-header">
            <div class="div-table-cell">ID</div>
            <div class="div-table-cell">Ссылка</div>
            <div class="div-table-cell">Дата</div>
            <div class="div-table-cell">Эпизоды</div>
            <div class="div-table-cell">Качество</div>
          </div>
          <div class="div-table-body">
            <div v-for="(t, i) in parserData.torrents" :key="t.torrent_id ?? i" class="div-table-row">
              <div class="div-table-cell">{{ t.torrent_id }}</div>
              <div class="div-table-cell">{{ t.link }}</div>
              <div class="div-table-cell">{{ t.date_time }}</div>
              <div class="div-table-cell">{{ t.episodes }}</div>
              <div class="div-table-cell">{{ t.quality }}</div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <template #footer>
      <Button label="Добавить" icon="pi pi-check" :loading="saving" :disabled="!canAddSeries || parsing" @click="addSeries" />
      <Button label="Отмена" icon="pi pi-times" severity="secondary" @click="emit('close')" />
    </template>
  </ModalShell>
</template>
