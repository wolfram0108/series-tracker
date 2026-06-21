<script setup lang="ts">
import { ref, reactive, computed, watch, onMounted } from "vue"
import { useToast } from "primevue/usetoast"
import StGroup from "../StGroup.vue"
import StIcon from "../StIcon.vue"
import StInput from "../StInput.vue"
import StSelect from "../StSelect.vue"
import SavedPathDropdown from "../SavedPathDropdown.vue"
import { api } from "../../api/client"
import { useApi } from "../../composables/useApi"
import type { Series } from "../../stores/series"

// Вкладка «Свойства» статус-окна (Шаг 1, торрент-путь + VK-поля).
// Редактирует существующий сериал: имя/путь/сезон/качество-оверрайды/
// профиль + TMDB-виджет (как в Add). Сохранение — POST /api/series/{id}
// (catalog.update + metadata.map + relocate/renaming). save() вызывается
// из футера StatusModal через ref и возвращает успех.
const props = defineProps<{ series: Series }>()
const emit = defineEmits<{ (e: "saving", v: boolean): void }>()
const { request } = useApi()
const toast = useToast()

interface TmdbResult { id: number; name?: string; name_en?: string; original_name?: string; year?: string; poster_path?: string }
interface TmdbSeason { season_number: number; episode_count: number }
interface Torrent { torrent_id?: string | number; link?: string; date_time?: string; episodes?: string; quality?: string; is_active?: boolean }
type VState = "valid" | "invalid" | null

const s = props.series
const isVk = s.source_type === "vk_video"

// --- редактируемые поля (инициализация из серии) ---
const form = reactive({
  name: String(s.name ?? ""),
  name_en: String((s as Record<string, unknown>).name_en ?? ""),
  save_path: String((s as Record<string, unknown>).save_path ?? ""),
  season: String(s.season ?? ""),
  quality: String((s as Record<string, unknown>).quality ?? ""),
  quality_override: String((s as Record<string, unknown>).quality_override ?? ""),
  resolution_override: String((s as Record<string, unknown>).resolution_override ?? ""),
  parser_profile_id: ((s as Record<string, unknown>).parser_profile_id ?? null) as number | null,
  vk_search_mode: String((s as Record<string, unknown>).vk_search_mode ?? "search"),
})
const site = String((s as Record<string, unknown>).site ?? "")
const trackerInfo = (s as Record<string, unknown>).tracker_info as { ui_features?: { quality_selector?: string } } | null
const qualitySelector = computed(() => trackerInfo?.ui_features?.quality_selector || "")

// --- доступные раздачи с сайта + выбор качества (порт StatusTabProperties) ---
const allSiteTorrents = ref<Torrent[]>([])
const siteTorrentsLoading = ref(false)
const siteDataIsStale = ref(false)
const anilibriaQ = ref<string[]>([]) // варианты качества (anilibria/rutracker)
const astarQ = reactive<Record<string, string[]>>({}) // по группам эпизодов (astar)
const qualityByEpisodes = reactive<Record<string, string>>({})

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
const sortedQualityOptionsKeys = computed(() => (site.includes("astar") ? sortEpisodeKeys(Object.keys(astarQ)) : []))
const qualityOptionsAnilibria = computed(() => anilibriaQ.value.map((q) => ({ label: q, value: q })))
const astarHasAlternatives = computed(() => Object.values(astarQ).some((o) => o.length > 1))
const filteredSiteTorrents = computed(() => {
  if (!allSiteTorrents.value.length) return []
  if (site.includes("anilibria") || site.includes("aniliberty")) {
    return allSiteTorrents.value.filter((t) => t.quality === form.quality)
  }
  if (site.includes("astar")) {
    const choices = Object.values(qualityByEpisodes)
    return allSiteTorrents.value.filter((t) =>
      (astarQ[t.episodes ?? ""] || []).length <= 1 ? choices.includes(t.quality ?? "") : t.quality === qualityByEpisodes[t.episodes ?? ""],
    )
  }
  return allSiteTorrents.value
})

// предвыбор качества из сохранённого (как _buildQualityOptions)
function buildQualityOptions(torrents: Torrent[]) {
  if (site.includes("anilibria") || site.includes("aniliberty") || site.includes("rutracker")) {
    anilibriaQ.value = [...new Set(torrents.filter((t) => t.quality).map((t) => t.quality as string))]
    if (!form.quality || !anilibriaQ.value.includes(form.quality)) form.quality = anilibriaQ.value[0] || ""
  } else if (site.includes("astar")) {
    Object.keys(astarQ).forEach((k) => delete astarQ[k])
    torrents.forEach((t) => {
      if (t.episodes) {
        if (!astarQ[t.episodes]) astarQ[t.episodes] = []
        if (t.quality) astarQ[t.episodes].push(t.quality)
      }
    })
    const saved = form.quality ? form.quality.split(";") : []
    let i = 0
    sortedQualityOptionsKeys.value.forEach((ep) => {
      const opts = astarQ[ep]
      if (opts.length > 1) {
        qualityByEpisodes[ep] = saved[i] || opts.find((q) => q !== "old") || opts[0]
        i++
      } else {
        qualityByEpisodes[ep] = opts[0]
      }
    })
  }
}

async function refreshSiteTorrents() {
  siteTorrentsLoading.value = true
  siteDataIsStale.value = false
  try {
    const hist = (await request(
      api.GET("/api/series/{series_id}/torrents/history", { params: { path: { series_id: Number(s.id) } } } as never),
    )) as Torrent[] | null
    if (Array.isArray(hist)) {
      allSiteTorrents.value = hist
      buildQualityOptions(hist)
    }
    const { data } = await api.POST("/api/parse_url", { body: { url } } as never)
    const res = (data ?? null) as { success?: boolean; torrents?: Torrent[]; error?: string } | null
    if (res?.success && Array.isArray(res.torrents)) {
      allSiteTorrents.value = res.torrents
      buildQualityOptions(res.torrents)
    } else {
      siteDataIsStale.value = true
    }
  } catch {
    siteDataIsStale.value = true
  } finally {
    siteTorrentsLoading.value = false
  }
}
const url = String((s as Record<string, unknown>).url ?? "")
const isSeasonless = ref(!form.season)
const showValidation = ref(false)

// VK: канал|запрос из url
const vkChannelUrl = ref("")
const vkQuery = ref("")
if (isVk && url) {
  const [ch, q] = url.split("|")
  vkChannelUrl.value = ch ?? ""
  vkQuery.value = q ?? ""
}

const profiles = ref<{ id: number; name: string }[]>([])
const profileOptions = computed(() => [
  { label: "Выберите профиль...", value: null as number | null },
  ...profiles.value.map((p) => ({ label: p.name, value: p.id as number | null })),
])

// --- TMDB (как в Add) ---
const tmdbSearchQuery = ref("")
const tmdbResults = ref<TmdbResult[]>([])
const tmdbSelected = ref<TmdbResult | null>(null)
const tmdbLoading = ref(false)
const tmdbEpisodeCount = ref(0)
const tmdbSeasonDetails = ref<TmdbSeason[]>([])

// предзаполнение из сохранённого tmdb_info
const info = (s as Record<string, unknown>).tmdb_info as Record<string, unknown> | null
if (info && info.tmdb_id) {
  tmdbSelected.value = {
    id: Number(info.tmdb_id),
    name: String(info.series_name ?? ""),
    year: info.year ? String(info.year) : undefined,
    poster_path: info.poster_path ? String(info.poster_path) : undefined,
  }
  tmdbEpisodeCount.value = Number(info.total_episodes ?? 0)
}

const tmdbSeasonNumber = computed(() => {
  if (isSeasonless.value) return 1
  const m = form.season.match(/^s(\d+)$/i)
  return m ? parseInt(m[1], 10) : 1
})
function tmdbName(r: TmdbResult): string {
  const ru = (r.name || "").trim()
  const orig = (r.original_name || "").trim()
  if (ru && ru !== orig) return ru
  const en = (r.name_en || "").trim()
  if (en && en !== orig) return en
  return ru || en || orig
}
const tmdbCatalogName = computed(() => {
  if (!tmdbSelected.value) return ""
  const name = tmdbName(tmdbSelected.value)
  if (!name) return ""
  const year = String(tmdbSelected.value.year || "").trim()
  const idPart = `[tmdbid-${tmdbSelected.value.id}]`
  return year ? `${name} (${year}) ${idPart}` : `${name} ${idPart}`
})

// --- валидация ---
const isSeasonValid = computed(() => {
  if (isSeasonless.value || isVk) return true
  return /^s\d{2}$/.test(form.season.trim())
})
function vstate(ok: boolean): VState {
  return ok ? "valid" : "invalid"
}
const nameState = computed<VState>(() => (showValidation.value ? vstate(!!form.name) : null))
const nameEnState = computed<VState>(() => (showValidation.value ? vstate(!!form.name_en) : null))
const savePathState = computed<VState>(() => (showValidation.value ? vstate(!!form.save_path) : null))
const seasonState = computed<VState>(() => {
  if (!showValidation.value || isSeasonless.value || isVk) return null
  return vstate(isSeasonValid.value)
})

watch(() => form.save_path, (v) => {
  if (v.includes("\\")) form.save_path = v.replace(/\\/g, "/")
})

async function loadProfiles() {
  const { data } = await api.GET("/api/parser-profiles")
  if (Array.isArray(data)) profiles.value = data as { id: number; name: string }[]
}

async function searchTMDB() {
  if (!tmdbSearchQuery.value) tmdbSearchQuery.value = form.name || form.name_en
  if (!tmdbSearchQuery.value) return
  tmdbLoading.value = true
  tmdbResults.value = []
  try {
    const { data } = await api.POST("/api/tmdb/search", { body: { query: tmdbSearchQuery.value } } as never)
    const res = (data ?? null) as { success?: boolean; results?: TmdbResult[]; error?: string } | null
    if (res?.success) tmdbResults.value = res.results ?? []
    else toast.add({ severity: "warn", summary: "TMDB", detail: `Ошибка поиска: ${res?.error ?? "?"}`, life: 4000 })
  } finally {
    tmdbLoading.value = false
  }
}
async function selectTMDBSeries(r: TmdbResult) {
  tmdbSelected.value = r
  const { data } = await api.GET("/api/tmdb/details/{tmdb_id}", { params: { path: { tmdb_id: r.id } } })
  const res = (data ?? null) as { success?: boolean; seasons?: TmdbSeason[] } | null
  if (res?.success) {
    tmdbSeasonDetails.value = res.seasons ?? []
    updateEpisodeCount()
  }
}
function clearTMDBSelection() {
  tmdbSelected.value = null
  tmdbSeasonDetails.value = []
  tmdbEpisodeCount.value = 0
}
function updateEpisodeCount() {
  if (!tmdbSelected.value || !tmdbSeasonDetails.value.length) return
  const sn = tmdbSeasonDetails.value.find((x) => x.season_number === tmdbSeasonNumber.value)
  tmdbEpisodeCount.value = sn ? sn.episode_count : 0
}
watch(() => form.season, updateEpisodeCount)
async function copyCatalogName() {
  if (!tmdbCatalogName.value) return
  try {
    await navigator.clipboard.writeText(tmdbCatalogName.value)
    toast.add({ severity: "success", summary: "Скопировано", detail: "Имя каталога в буфере", life: 2500 })
  } catch {
    toast.add({ severity: "error", summary: "Ошибка", detail: "Не удалось скопировать", life: 2500 })
  }
}

// url-действия (торрент)
async function copyUrl() {
  try {
    await navigator.clipboard.writeText(url)
    toast.add({ severity: "success", summary: "Скопировано", detail: "Ссылка в буфере", life: 2000 })
  } catch {
    /* ignore */
  }
}
function openUrl() {
  window.open(url, "_blank")
}

const canSave = computed(() => {
  const base = !!form.name && !!form.name_en && !!form.save_path
  if (isVk) return base && !!vkChannelUrl.value && !!form.parser_profile_id
  return base && isSeasonValid.value
})

// вызывается из футера StatusModal; возвращает успех
async function save(): Promise<boolean> {
  showValidation.value = true
  if (!canSave.value) {
    toast.add({ severity: "error", summary: "Проверьте поля", detail: "Заполните обязательные поля.", life: 4000 })
    return false
  }
  emit("saving", true)
  try {
    const payload: Record<string, unknown> = {
      name: form.name,
      name_en: form.name_en,
      save_path: form.save_path,
      season: isSeasonless.value ? "" : form.season,
      quality_override: form.quality_override || null,
      resolution_override: form.resolution_override || null,
      parser_profile_id: form.parser_profile_id,
      vk_search_mode: form.vk_search_mode,
    }
    if (isVk) {
      payload.url = `${vkChannelUrl.value}|${vkQuery.value}`
    } else {
      // строка качества (как в оригинале): anilibria/rutracker → одно
      // значение; astar → версии мульти-групп + по одной из одиночных, ';'
      let quality = ""
      if (site.includes("anilibria") || site.includes("aniliberty") || site.includes("rutracker")) {
        quality = form.quality
      } else if (site.includes("astar")) {
        const multi = sortedQualityOptionsKeys.value
          .filter((ep) => astarQ[ep] && astarQ[ep].length > 1)
          .map((ep) => qualityByEpisodes[ep])
        const single = new Set(
          sortedQualityOptionsKeys.value.filter((ep) => astarQ[ep] && astarQ[ep].length === 1).map((ep) => astarQ[ep][0]),
        )
        quality = [...multi, ...Array.from(single)].join(";")
      }
      payload.quality = quality
    }
    if (tmdbSelected.value) {
      payload.tmdb_data = {
        tmdb_id: tmdbSelected.value.id,
        tmdb_season_number: tmdbSeasonNumber.value,
        total_episodes: tmdbEpisodeCount.value,
        poster_path: tmdbSelected.value.poster_path,
        series_name: tmdbName(tmdbSelected.value),
        year: tmdbSelected.value.year,
      }
    }
    const ok = await request(
      api.POST("/api/series/{series_id}", { params: { path: { series_id: Number(s.id) } }, body: payload } as never),
      { errorMessage: "Ошибка сохранения" },
    )
    if (ok !== null) {
      toast.add({ severity: "success", summary: "Сохранено", detail: "Изменения приняты в обработку", life: 3000 })
      return true
    }
    return false
  } finally {
    emit("saving", false)
  }
}

defineExpose({ save })
onMounted(() => {
  void loadProfiles()
  if (!isVk) void refreshSiteTorrents() // автопарсинг доступных раздач + качество
})
</script>

<template>
  <div class="status-properties">
    <!-- VK-настройки -->
    <div v-if="isVk" class="modern-fieldset">
      <div class="fieldset-header"><span class="fieldset-title">Настройки для VK Video</span></div>
      <div class="fieldset-content">
        <label class="modern-label">Режим поиска</label>
        <div class="vk-mode-group">
          <button
            type="button"
            class="vk-mode-btn"
            :class="{ active: form.vk_search_mode === 'search' }"
            @click="form.vk_search_mode = 'search'"
          >
            <i class="pi pi-search" /> Быстрый поиск
          </button>
          <button
            type="button"
            class="vk-mode-btn"
            :class="{ active: form.vk_search_mode === 'get_all' }"
            @click="form.vk_search_mode = 'get_all'"
          >
            <i class="pi pi-list" /> Полное сканирование
          </button>
        </div>
        <div class="add-grid-2 mt-3">
          <StGroup>
            <StIcon icon="pi pi-youtube" />
            <StInput v-model="vkChannelUrl" label="Ссылка на канал" />
          </StGroup>
          <StGroup>
            <StIcon icon="pi pi-search" />
            <StInput v-model="vkQuery" label="Поисковые запросы (через /)" />
          </StGroup>
        </div>
      </div>
    </div>

    <!-- Информация -->
    <div class="modern-fieldset">
      <div class="fieldset-header"><span class="fieldset-title">Информация о сериале</span></div>
      <div class="fieldset-content">
        <div class="add-grid-2">
          <StGroup :state="nameState">
            <StIcon icon="pi pi-pencil" />
            <StInput v-model="form.name" label="Название (RU)" />
          </StGroup>
          <StGroup :state="nameEnState">
            <StIcon icon="pi pi-language" />
            <StInput v-model="form.name_en" label="Название (EN)" />
          </StGroup>
        </div>
        <div class="add-grid-2 mt-3">
          <StGroup :state="savePathState">
            <StIcon icon="pi pi-folder-open" />
            <StInput v-model="form.save_path" label="Путь сохранения" />
            <SavedPathDropdown :catalog-name="tmdbCatalogName" @select="form.save_path = $event" />
          </StGroup>
          <StGroup :state="seasonState" :class="{ 'is-disabled': isSeasonless }">
            <StIcon icon="pi pi-hashtag" />
            <StInput v-model="form.season" label="Сезон (формат s01)" />
          </StGroup>
        </div>

        <!-- оверрайды качества/разрешения -->
        <div class="add-grid-2 mt-3">
          <StGroup>
            <StIcon icon="pi pi-video" />
            <StInput v-model="form.quality_override" label="Качество (ручной ввод)" />
          </StGroup>
          <StGroup>
            <StIcon icon="pi pi-desktop" />
            <StInput v-model="form.resolution_override" label="Разрешение (ручной ввод)" />
          </StGroup>
        </div>

        <!-- профиль правил (торрент и VK: оба используют parser_profile_id) -->
        <div class="field-group mt-3">
          <StGroup>
            <div class="constructor-item item-label-text-icon" title="Профиль правил">
              <i class="pi pi-filter" /><span>Профиль правил</span>
            </div>
            <StSelect v-model="form.parser_profile_id" :options="profileOptions" placeholder="Выберите профиль..." />
          </StGroup>
        </div>

        <!-- url (торрент): readonly + копировать/открыть -->
        <div v-if="!isVk && url" class="field-group mt-3">
          <StGroup>
            <StIcon icon="pi pi-link" />
            <div class="constructor-item item-floating-label">
              <input class="item-input" :value="url" readonly />
            </div>
            <div class="constructor-item url-actions">
              <button type="button" class="url-action-btn" title="Скопировать" @click="copyUrl"><i class="pi pi-clipboard" /></button>
              <button type="button" class="url-action-btn" title="Открыть" @click="openUrl"><i class="pi pi-external-link" /></button>
            </div>
          </StGroup>
        </div>
      </div>
    </div>

    <!-- несколько сезонов — отдельной секцией (как в окне добавления) -->
    <div class="modern-fieldset">
      <div class="fieldset-content">
        <label class="modern-form-check">
          <input v-model="isSeasonless" type="checkbox" class="form-switch-input" />
          <span class="modern-form-check-label">Раздача содержит несколько сезонов (или сезон не важен)</span>
        </label>
      </div>
    </div>

    <!-- Выбор качества (торрент, по tracker_info.ui_features) -->
    <div v-if="!isVk && qualitySelector === 'anilibria'" class="modern-fieldset">
      <div class="fieldset-header"><span class="fieldset-title">Выбор качества</span></div>
      <div class="fieldset-content">
        <div v-if="qualityOptionsAnilibria.length" class="field-group">
          <StGroup>
            <div class="constructor-item item-label">Качество</div>
            <StSelect
              :model-value="form.quality"
              :options="qualityOptionsAnilibria"
              @update:model-value="form.quality = $event as string"
            />
          </StGroup>
        </div>
        <div v-else-if="!siteTorrentsLoading" class="add-error">Качества не найдены</div>
      </div>
    </div>
    <div v-if="!isVk && qualitySelector === 'astar'" class="modern-fieldset">
      <div class="fieldset-header"><span class="fieldset-title">Выбор качества</span></div>
      <div class="fieldset-content">
        <template v-for="ep in sortedQualityOptionsKeys" :key="ep">
          <div v-if="astarQ[ep] && astarQ[ep].length > 1" class="field-group mt-2">
            <StGroup>
              <div class="constructor-item item-label">Эпизоды {{ ep }}</div>
              <StSelect
                :model-value="qualityByEpisodes[ep]"
                :options="astarQ[ep].map((q) => ({ label: q, value: q }))"
                @update:model-value="qualityByEpisodes[ep] = $event as string"
              />
            </StGroup>
          </div>
        </template>
        <div v-if="!siteTorrentsLoading && !astarHasAlternatives" class="fieldset-hint">
          Для данного релиза нет альтернативных версий.
        </div>
      </div>
    </div>

    <!-- Доступные раздачи с сайта (торрент) -->
    <div v-if="!isVk" class="add-torrents">
      <h6 class="add-torrents-title">Раздачи с сайта (по выбранному качеству)</h6>
      <div v-if="siteTorrentsLoading" class="status-loading">
        <i class="pi pi-spin pi-spinner" /> Обновляем список с сайта…
      </div>
      <template v-else>
        <div class="div-table table-site-torrents">
          <div class="div-table-header">
            <div class="div-table-cell">ID</div>
            <div class="div-table-cell">Ссылка</div>
            <div class="div-table-cell">Дата</div>
            <div class="div-table-cell">Эпизоды</div>
            <div class="div-table-cell">Качество</div>
          </div>
          <div class="div-table-body">
            <div v-for="(t, i) in filteredSiteTorrents" :key="t.torrent_id ?? i" class="div-table-row" :class="{ 'row-stale': siteDataIsStale }">
              <div class="div-table-cell">{{ t.torrent_id }}</div>
              <div class="div-table-cell">{{ t.link }}</div>
              <div class="div-table-cell">{{ t.date_time }}</div>
              <div class="div-table-cell">{{ t.episodes }}</div>
              <div class="div-table-cell">{{ t.quality }}</div>
            </div>
            <div v-if="!filteredSiteTorrents.length" class="div-table-row"><div class="div-table-cell" style="grid-column: 1 / -1">Раздач не найдено</div></div>
          </div>
        </div>
        <small v-if="siteDataIsStale" class="add-error" style="display: block; margin-top: 8px">
          Не удалось обновить данные с сайта — показана последняя сохранённая версия.
        </small>
      </template>
    </div>

    <!-- TMDB -->
    <div class="modern-fieldset">
      <div class="fieldset-header"><span class="fieldset-title">Синхронизация с TMDB</span></div>
      <div class="fieldset-content">
        <div class="input-group mb-3">
          <input v-model="tmdbSearchQuery" type="text" class="form-control" placeholder="Название сериала" @keyup.enter="searchTMDB" />
          <button type="button" class="input-group-btn" :disabled="tmdbLoading" @click="searchTMDB">
            <i class="pi" :class="tmdbLoading ? 'pi-spin pi-spinner' : 'pi-search'" /> Найти
          </button>
        </div>

        <div v-if="tmdbResults.length" class="tmdb-results">
          <div
            v-for="res in tmdbResults"
            :key="res.id"
            class="card-final card-tmdb tmdb-result-card"
            :class="tmdbSelected && tmdbSelected.id === res.id ? 'status-success' : 'status-no-match'"
            @click="selectTMDBSeries(res)"
          >
            <div class="info-column">
              <span class="card-title">{{ tmdbName(res) }} <small style="opacity: 0.6">({{ res.year }})</small></span>
              <div class="path-line tmdb-pills">
                <span v-if="res.original_name" class="path-pill">
                  <span class="path-pill-label">Оригинал:</span>
                  <span class="path-pill-value">{{ res.original_name }}</span>
                </span>
                <span v-if="tmdbSelected && tmdbSelected.id === res.id" class="path-pill">
                  <span class="path-pill-label">Сезон {{ tmdbSeasonNumber }}:</span>
                  <span class="path-pill-value">{{ tmdbEpisodeCount }} эпизодов</span>
                </span>
              </div>
            </div>
            <div class="pills-column">
              <div class="quality-badge">ID: {{ res.id }}</div>
              <div v-if="tmdbSelected && tmdbSelected.id === res.id" class="pill"><i class="pi pi-check-circle" /><span>Выбран</span></div>
              <div v-if="tmdbSelected && tmdbSelected.id === res.id" class="pill pill-link" title="Сбросить" @click.stop="clearTMDBSelection">
                <i class="pi pi-times" /><span>Сброс</span>
              </div>
            </div>
          </div>
        </div>

        <!-- выбранный без поиска (предзаполнен из tmdb_info) -->
        <div
          v-else-if="tmdbSelected"
          class="card-final card-tmdb status-success"
        >
          <div class="info-column">
            <span class="card-title">{{ tmdbName(tmdbSelected) }} <small style="opacity: 0.6">({{ tmdbSelected.year }})</small></span>
            <div class="path-line">
              <span class="path-pill"><span class="path-pill-label">Сезон {{ tmdbSeasonNumber }}:</span><span class="path-pill-value">{{ tmdbEpisodeCount }} эпизодов</span></span>
            </div>
          </div>
          <div class="pills-column">
            <div class="quality-badge">ID: {{ tmdbSelected.id }}</div>
            <div class="pill"><i class="pi pi-check-circle" /><span>Выбран</span></div>
            <div class="pill pill-link" title="Сбросить" @click.stop="clearTMDBSelection"><i class="pi pi-times" /><span>Сброс</span></div>
          </div>
        </div>

        <div v-if="tmdbSelected && tmdbCatalogName" class="tmdb-catalog">
          <label class="tmdb-catalog-label">Имя каталога</label>
          <div class="input-group">
            <input class="form-control" :value="tmdbCatalogName" readonly @focus="($event.target as HTMLInputElement).select()" />
            <button type="button" class="input-group-btn" title="Скопировать" @click="copyCatalogName"><i class="pi pi-clipboard" /></button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
