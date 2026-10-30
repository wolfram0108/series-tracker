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
import SettingsParser from "./settings/SettingsParser.vue"
import WebSourcesEditor from "./WebSourcesEditor.vue"
import { api } from "../api/client"
import { t } from "../i18n"
import { reasonText } from "../lib/reason"
import { blocksAdd, sourceRows, type SourcesLayout } from "../lib/webSources"
import type { components } from "../api/schema"
import { useApi } from "../composables/useApi"
import { useSSE } from "../composables/useSSE"

// Модалка «Добавить сериал» — полный порт legacy addSeriesModal.js.
// Поток: ввод URL → debounce-распознавание (vkvideo.ru → VK-режим без
// отдельного API, парсинг ссылки в JS; иначе торрент-режим, POST
// /api/parse_url) → инфо о сериале → выбор качества по трекеру → опц.
// синхронизация с TMDB → сохранение POST /api/series.
const emit = defineEmits<{ (e: "close"): void; (e: "created"): void }>()
const { request } = useApi()
const toast = useToast()

// --- типы ответов (бэк отдаёт DynamicObject; сужаем под контракт) ---
interface Torrent { torrent_id?: string | number; link?: string; magnet?: string; date_time?: string; episodes?: string; quality?: string }
interface ParseResult { success?: boolean; title?: { ru?: string; en?: string }; torrents?: Torrent[]; tracker_info?: TrackerInfo; error?: string }
interface TrackerInfo { ui_features?: { quality_selector?: string } }
interface TmdbResult { id: number; name?: string; name_en?: string; original_name?: string; year?: string; poster_path?: string }
interface TmdbSeason { season_number: number; episode_count: number }

// --- состояние формы ---
const newSeries = reactive({
  url: "",
  save_path: "",
  name: "",
  name_en: "",
  season: "s01",
  quality_override: "",
  resolution_override: "",
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
const sourceType = ref<"torrent" | "vk_video" | "web_video">("torrent")
const webVideoTitles = ref<string[]>([])
const canonicalUrl = ref("")
const vkChannelUrl = ref("")
const vkQuery = ref("")
const trackerInfo = ref<TrackerInfo | null>(null)
type PlayerReport = { present: boolean; available: boolean | null; resolution: number | null; reason?: string; enabled?: boolean; toggle?: string }
const parsePlayers = ref<Record<string, PlayerReport>>({})
// A provider may describe its sources apart from the parse because reading them is slow (a shared catalog).
const hasLayout = ref(false)
const sourcesLayout = ref<SourcesLayout | null>(null)
const layoutLoading = ref(false)
let layoutToken = 0
// Player/voice-over choices of a web source, delivered with the link parse.
type WebSource = components["schemas"]["ReleaseSource"]
type WebLayout = components["schemas"]["Layout"]
const webSources = ref<WebSource[]>([])
const webLayout = ref<WebLayout | null>(null)
const selectedSource = ref("")
const profiles = ref<{ id: number; name: string }[]>([])
const saving = ref(false)

// --- вкладки окна: форма добавления / раздел «Фильтры VK» (проверка нейминга) ---
const activeTab = ref<"add" | "naming">("add")
// списки файлов раздач по torrent_id (приходят по SSE после фонового префетча)
type PreviewEntry = { files?: { path: string; size?: number }[]; failed?: boolean; reason?: string }
const previewResults = ref<Record<string, PreviewEntry>>({})
const sse = useSSE()
let unsubReady: (() => void) | null = null
let unsubFailed: (() => void) | null = null

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
  if (sourceType.value === "vk_video" || sourceType.value === "web_video") return true
  if (sourceType.value === "torrent" && parsed.value) return true
  return false
})
const isSeasonValid = computed(() => {
  if (isSeasonless.value || sourceType.value === "vk_video" || sourceType.value === "web_video") return true
  return /^s\d{2}$/.test(newSeries.season.trim())
})
const canAddSeries = computed(() => {
  const base = !!newSeries.name && !!newSeries.name_en && isAbsolutePath(newSeries.save_path)
  if (sourceType.value === "vk_video") {
    const vkOk = !!vkChannelUrl.value && !!newSeries.parser_profile_id
    const searchOk = newSeries.vk_search_mode === "search" ? !!vkQuery.value : true
    return base && vkOk && searchOk
  }
  if (sourceType.value === "web_video") {
    return parsed.value && base && !!newSeries.parser_profile_id && !(hasLayout.value && blocksAdd(sourceRowsView.value))
  }
  return parsed.value && base && isSeasonValid.value
})
const sourceRowsView = computed(() => sourceRows(parsePlayers.value, sourcesLayout.value, layoutLoading.value))
const sortedQualityOptionsKeys = computed(() => {
  if (!site.value.includes("astar")) return []
  return sortEpisodeKeys(Object.keys(episodeQualityOptions))
})
const tmdbSeasonNumber = computed(() => {
  if (isSeasonless.value) return 1
  const m = newSeries.season.match(/^s(\d+)$/i)
  return m ? parseInt(m[1], 10) : 1
})
// Отображаемое имя: русское, если перевод реально есть (name ≠ original);
// иначе английское (для азиатских релизов без ru TMDB кладёт в name
// оригинал-иероглифы); иначе — что есть. Применяется и в списке, и в
// имени каталога.
function tmdbName(r: TmdbResult): string {
  const ru = (r.name || "").trim()
  const orig = (r.original_name || "").trim()
  if (ru && ru !== orig) return ru
  const en = (r.name_en || "").trim()
  if (en && en !== orig) return en
  return ru || en || orig
}

// «Имя (год) [tmdbid-XXXX]» (шаблон Jellyfin/Plex); год опускается, если его нет
const tmdbCatalogName = computed(() => {
  if (!tmdbSelected.value) return ""
  const name = tmdbName(tmdbSelected.value)
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
function isAbsolutePath(value: string): boolean {
  return value.trim().startsWith("/")
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
const savePathState = computed<VState>(() => (shouldShowValidation.value ? vstate(isAbsolutePath(newSeries.save_path)) : null))
const seasonState = computed<VState>(() => {
  if (!shouldShowValidation.value || isSeasonless.value || sourceType.value === "vk_video" || sourceType.value === "web_video") return null
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
  () => sourceType.value === "vk_video" || sourceType.value === "web_video" || (!!site.value && (site.value.includes("kinozal") || site.value.includes("rutracker"))),
)
const astarHasAlternatives = computed(() => Object.values(episodeQualityOptions).some((o) => o.length > 1))

// --- предпросмотр нейминга: список файлов раздач(и) выбранного качества ---
function baseName(p: string): string {
  return p.split("/").pop() || p
}
// torrent_id раздач, соответствующих текущему выбору качества (по трекеру)
const selectedTorrentIds = computed<string[]>(() => {
  const ts = parserData.value?.torrents ?? []
  if (!ts.length) return []
  const idOf = (t: Torrent) => String(t.torrent_id ?? "")
  if (qualitySelector.value === "anilibria") {
    return ts.filter((t) => t.quality === newSeries.qualityByEpisodes.all).map(idOf)
  }
  if (site.value.includes("rutracker")) {
    const m = ts.filter((t) => t.quality === newSeries.qualityByEpisodes.all).map(idOf)
    return m.length ? m : ts.map(idOf)
  }
  if (qualitySelector.value === "astar") {
    const chosen = new Set(sortedQualityOptionsKeys.value.map((ep) => `${ep}|${newSeries.qualityByEpisodes[ep]}`))
    return ts.filter((t) => t.episodes && chosen.has(`${t.episodes}|${t.quality}`)).map(idOf)
  }
  return ts.map(idOf) // kinozal и пр. без селектора — обычно одна раздача
})
// имена файлов (basename) для поля шага 3 теста
const namingTitles = computed<string[]>(() => {
  if (sourceType.value === "web_video" && webVideoTitles.value.length) {
    return webVideoTitles.value
  }
  const out: string[] = []
  for (const id of selectedTorrentIds.value) {
    const r = previewResults.value[id]
    if (r?.files?.length) out.push(...r.files.map((f) => baseName(f.path)))
  }
  return out
})

// Player 1 is CVH, player 2 is Kodik: the numbering users go by.
const playerLabel = (player: string) =>
  player === "cvh" || player === "kodik" ? t(`add.source.player.${player}`) : player
const kindLabel = (kind: string) =>
  kind === "voice" || kind === "subtitles" ? t(`add.source.kind.${kind}`) : kind
// `ranges` is the real coverage with holes, e.g. "1-10, 13-20".
const coverageText = (s: WebSource) =>
  s.count ? t("add.source.coverage", { ranges: s.ranges, count: s.count }) : t("add.source.none")
const webSourceOptions = computed(() => webSources.value.map((s) => ({
  value: s.url,
  label: t("add.source.option", {
    player: playerLabel(s.player), studio: s.studio,
    kind: kindLabel(s.kind) ? ` · ${kindLabel(s.kind)}` : "", coverage: coverageText(s),
  }),
})))

const selectedWebSource = computed(() => webSources.value.find((s) => s.url === selectedSource.value) ?? null)

// отчёт о плеерах web-источника (приходит с разбором ссылки)
const playerReportRows = computed(() => {
  const labels: Record<string, string> = { vk: "VK", allvideo: "AllVideo", vibix: "Vibix", cvh: t("player.cvh") }
  return Object.entries(parsePlayers.value).map(([name, report]) => {
    let text = reasonText(report.reason) || t("player.state.unavailable")
    if (!report.present) text = t("player.state.absent")
    else if (report.available === null) text = t("player.state.pending")
    else if (report.available) text = report.resolution ? `${report.resolution}p` : t("player.state.available")
    return {
      name,
      label: labels[name] ?? name.toUpperCase(),
      text,
      ok: report.available === true,
      off: report.available === false,
    }
  })
})

// A backslash in a pasted channel URL is a typo for a slash (legacy autoCorrectSlash).
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
  webVideoTitles.value = []
  canonicalUrl.value = ""
  parsePlayers.value = {}
  webSources.value = []
  selectedSource.value = ""
  webLayout.value = null
  hasLayout.value = false
  sourcesLayout.value = null
  layoutLoading.value = false
  layoutToken++
  newSeries.resolution_override = ""
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

interface WebParse {
  titles?: string[]
  canonical_url?: string
  players?: Record<string, PlayerReport>
  sources?: WebSource[]
  selected_source?: string
  layout?: WebLayout | null
  has_layout?: boolean
}
function applyWebParse(res: WebParse) {
  webVideoTitles.value = res.titles ?? []
  canonicalUrl.value = res.canonical_url || ""
  parsePlayers.value = res.players ?? {}
  webSources.value = res.sources ?? []
  selectedSource.value = res.selected_source ?? ""
  webLayout.value = res.layout ?? null
  hasLayout.value = !!res.has_layout
  if (hasLayout.value) void loadLayout(res.canonical_url || newSeries.url)
  else sourcesLayout.value = null
}

// The sources of a release that the provider reads apart from the parse; a newer request wins.
async function loadLayout(address: string) {
  const token = ++layoutToken
  layoutLoading.value = true
  sourcesLayout.value = null
  let layout: SourcesLayout = { sources: [], reason: "failed" }
  try {
    const { data } = await api.POST("/api/web_video/layout", { body: { url: address } } as never)
    if (data) layout = data as unknown as SourcesLayout
  } catch {
    // The failed read stays a layout without sources: the player is shown as unavailable.
  }
  if (token !== layoutToken) return
  sourcesLayout.value = layout
  layoutLoading.value = false
}

// Switching the choice re-parses the link; the form fields stay untouched.
async function pickSource(address: string) {
  const previous = selectedSource.value
  selectedSource.value = address
  urlError.value = ""
  parsing.value = true
  try {
    const { data, error } = await api.POST("/api/parse_url", { body: { url: address } } as never)
    const res = (data ?? null) as WebParse | null
    if (error || !res) {
      const err = (error ?? null) as { error?: string } | null
      throw new Error(reasonText(err?.error) || t("add.error.parse"))
    }
    applyWebParse(res)
  } catch (e) {
    selectedSource.value = previous
    urlError.value = e instanceof Error ? e.message : String(e)
  } finally {
    parsing.value = false
  }
}

async function parseTorrentUrl() {
  parsing.value = true
  parsePlayers.value = {}
  try {
    site.value = new URL(newSeries.url).hostname.replace(/^www\./, "")
  } catch {
    urlError.value = "Некорректный URL"
    parsing.value = false
    return
  }
  try {
    const { data, error } = await api.POST("/api/parse_url", { body: { url: newSeries.url } } as never)
    const res = (data ?? null) as (ParseResult & {
      source_type?: string
      titles?: string[]
      canonical_url?: string
      resolution?: number | string | null
    }) | null
    if (error || !res) {
      const err = (error ?? null) as { error?: string } | null
      throw new Error(reasonText(err?.error) || t("add.error.parse"))
    }
    if (res.source_type === "web_video") {
      sourceType.value = "web_video"
      applyWebParse(res)
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
    if (res.source_type !== "web_video") {
      void prefetchNamingFiles(torrents)  // фоновый добор списков файлов для вкладки проверки
    }
  } catch (e) {
    urlError.value = e instanceof Error ? e.message : String(e)
  } finally {
    parsing.value = false
  }
}

// Фоновый префетч списков файлов раздач (для вкладки «Проверка нейминга»):
// http(s)-ссылка → .torrent-ветка; magnet → добор через qBit. Результаты
// приходят по SSE (preview_naming_ready/failed) в previewResults.
async function prefetchNamingFiles(torrents: Torrent[]): Promise<void> {
  previewResults.value = {}
  const items = torrents
    .map((t) => {
      const url = t.link || ""
      if (/^https?:/i.test(url)) return { torrent_id: t.torrent_id, link: url }
      const magnet = t.magnet || (url.startsWith("magnet:") ? url : "")
      return magnet ? { torrent_id: t.torrent_id, magnet } : null
    })
    .filter((it): it is NonNullable<typeof it> => it !== null)
  if (!items.length) return
  await api.POST("/api/preview/naming/fetch", { body: { items } } as never)
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
      // пустой ручной ввод → null (как в edit-модалке), а не "" в БД
      quality_override: newSeries.quality_override || null,
      resolution_override: newSeries.resolution_override || null,
    }
    if (tmdbSelected.value) {
      payload.tmdb_data = {
        tmdb_id: tmdbSelected.value.id,
        tmdb_season_number: tmdbSeasonNumber.value,
        // «несколько сезонов»: число серий неизвестно до нейминга/скана —
        // не задаём (0), карточка не покажет, агрегатор посчитает позже.
        total_episodes: isSeasonless.value ? 0 : tmdbEpisodeCount.value,
        poster_path: tmdbSelected.value.poster_path,
        series_name: tmdbName(tmdbSelected.value),
        year: tmdbSelected.value.year,
      }
    }
    if (sourceType.value === "vk_video") {
      payload.source_type = "vk_video"
      payload.url = `${vkChannelUrl.value}|${vkQuery.value}`
      payload.site = "vkvideo.ru"
    } else if (sourceType.value === "web_video") {
      payload.source_type = "web_video"
      payload.url = canonicalUrl.value || newSeries.url
      payload.site = site.value
      payload.parser_profile_id = newSeries.parser_profile_id
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
onMounted(() => {
  loadProfiles()
  unsubReady = sse.on("preview_naming_ready", (d) => {
    const p = d as { torrent_id?: string | number; files?: { path: string; size?: number }[] }
    if (p.torrent_id != null) previewResults.value[String(p.torrent_id)] = { files: p.files ?? [] }
  })
  unsubFailed = sse.on("preview_naming_failed", (d) => {
    const p = d as { torrent_id?: string | number; reason?: string }
    if (p.torrent_id != null) previewResults.value[String(p.torrent_id)] = { failed: true, reason: p.reason, files: [] }
  })
})
onBeforeUnmount(() => {
  unsubReady?.()
  unsubFailed?.()
  if (debounceTimer) clearTimeout(debounceTimer)
})
</script>

<template>
  <ModalShell size="xl" @close="emit('close')">
    <template #title><i class="pi pi-plus"></i> Добавить новый сериал</template>
    <template #header-extra>
      <div class="header-tabs st-tabs">
        <button class="st-tab" :class="{ active: activeTab === 'add' }" title="Добавление" @click="activeTab = 'add'">
          <i class="pi tab-icon pi-plus-circle"></i>
          <span class="tab-label" data-text="Добавление"><span class="tl-text">Добавление</span></span>
        </button>
        <button class="st-tab" :class="{ active: activeTab === 'naming' }" title="Проверка нейминга" @click="activeTab = 'naming'">
          <i class="pi tab-icon pi-filter"></i>
          <span class="tab-label" data-text="Проверка нейминга"><span class="tl-text">Проверка нейминга</span></span>
        </button>
      </div>
    </template>

    <div v-show="activeTab === 'add'">
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
              <StInput v-model="newSeries.name_en" label="Название (EN)" kind="filename" />
            </StGroup>
          </div>
          <div class="add-grid-2 mt-3">
            <StGroup :state="savePathState">
              <StIcon icon="pi pi-folder-open" />
              <StInput v-model="newSeries.save_path" label="Путь сохранения" kind="path" />
              <SavedPathDropdown :catalog-name="tmdbCatalogName" @select="newSeries.save_path = $event" />
            </StGroup>
            <StGroup :state="seasonState" :class="{ 'is-disabled': isSeasonless }">
              <StIcon icon="pi pi-hashtag" />
              <StInput v-model="newSeries.season" label="Сезон (формат s01)" />
            </StGroup>
          </div>
          <div v-if="newSeries.save_path && !isAbsolutePath(newSeries.save_path)" class="add-error">
            Путь должен быть абсолютным и начинаться с «/», например /nas/...
          </div>
          <!-- оверрайды качества/разрешения (ручной ввод; как в инфо-модалке) -->
          <div class="add-grid-2 mt-3">
            <StGroup>
              <StIcon icon="pi pi-video" />
              <StInput v-model="newSeries.quality_override" label="Качество (ручной ввод)" kind="filename" />
            </StGroup>
            <StGroup>
              <StIcon icon="pi pi-desktop" />
              <StInput v-model="newSeries.resolution_override" label="Разрешение (ручной ввод)" kind="filename" />
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

      <!-- Источники web-видео -->
      <div v-if="sourceType === 'web_video' && playerReportRows.length" class="modern-fieldset">
        <div class="fieldset-header"><span class="fieldset-title">Источники</span></div>
        <div class="fieldset-content">
          <WebSourcesEditor v-if="hasLayout" :rows="sourceRowsView" :busy="parsing" @pick="pickSource" />
          <template v-else>
          <div v-if="webSourceOptions.length" class="field-group mb-3">
            <StGroup>
              <div class="constructor-item item-label-icon item-label-text-icon" :title="t('add.source.label')">
                <i class="pi pi-volume-up" /><span>{{ t("add.source.label") }}</span>
              </div>
              <StSelect
                :model-value="selectedSource"
                :options="webSourceOptions"
                @update:model-value="pickSource($event as string)"
              />
            </StGroup>
            <!-- the list row is one line; the full coverage of the chosen voice-over wraps here -->
            <div v-if="selectedWebSource?.count" class="fieldset-hint mt-2">
              {{ t("add.source.coverageHint", { ranges: selectedWebSource.ranges, count: selectedWebSource.count }) }}
            </div>
          </div>
          <div class="player-report">
            <span
              v-for="p in playerReportRows"
              :key="p.name"
              class="player-report-item"
              :class="{ 'is-ok': p.ok, 'is-off': p.off }"
            >
              <b>{{ p.label }}</b><span>{{ p.text }}</span>
            </span>
          </div>
          <div class="fieldset-hint">
            Предварительная проверка по первой серии; точное качество — после скана.
          </div>
          </template>
        </div>
      </div>

      <!-- Layout of voice-overs: what is the reference, what is downloaded and aligned -->
      <div v-if="sourceType === 'web_video' && webLayout?.voices.length" class="modern-fieldset">
        <div class="fieldset-header"><span class="fieldset-title">{{ t("add.layout.title") }}</span></div>
        <div class="fieldset-content">
          <div v-for="row in webLayout.voices" :key="row.label" class="player-report">
            <span class="player-report-item" :class="{ 'is-ok': row.role === 'reference' }">
              <b>{{ row.label }}</b>
              <span>{{ t("add.layout.row", { role: t(`add.layout.role.${row.role}`), player: playerLabel(row.source.player), coverage: coverageText(row.source) }) }}</span>
            </span>
            <span v-for="fallback in row.fallbacks" :key="fallback.url" class="player-report-item">
              <b>{{ t("add.layout.fallback") }}</b>
              <span>{{ t("add.layout.fallbackRow", { player: playerLabel(fallback.player), coverage: coverageText(fallback) }) }}</span>
            </span>
          </div>
          <div v-for="skipped in webLayout.skipped" :key="skipped.url" class="fieldset-hint">
            {{ t("add.layout.skipped", { studio: skipped.studio, player: playerLabel(skipped.player) }) }}
          </div>
          <div class="fieldset-hint">{{ t("add.layout.hint") }}</div>
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
                  <span v-if="tmdbSelected && tmdbSelected.id === res.id && !isSeasonless" class="path-pill">
                    <span class="path-pill-label">Сезон {{ tmdbSeasonNumber }}:</span>
                    <span class="path-pill-value">{{ tmdbEpisodeCount }} эпизодов</span>
                  </span>
                  <span v-else-if="tmdbSelected && tmdbSelected.id === res.id" class="path-pill">
                    <span class="path-pill-value">Несколько сезонов</span>
                  </span>
                </div>
              </div>
              <div class="pills-column">
                <div class="quality-badge">ID: {{ res.id }}</div>
                <div v-if="tmdbSelected && tmdbSelected.id === res.id" class="pill">
                  <i class="pi pi-check-circle" /><span>Выбран</span>
                </div>
                <div
                  v-if="tmdbSelected && tmdbSelected.id === res.id"
                  class="pill pill-link"
                  title="Сбросить выбор"
                  @click.stop="clearTMDBSelection"
                >
                  <i class="pi pi-times" /><span>Сброс</span>
                </div>
              </div>
            </div>
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
      <div v-if="sourceType === 'torrent' && parserData && parserData.torrents.length" class="modern-fieldset">
        <div class="fieldset-header"><span class="fieldset-title">Доступные торренты ({{ parserData.torrents.length }})</span></div>
        <div class="fieldset-content">
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
    </div>
    </div>

    <div v-show="activeTab === 'naming'">
      <SettingsParser
        :initial-profile-id="newSeries.parser_profile_id"
        :test-initial-titles="namingTitles"
        :test-initial-url="sourceType === 'web_video' ? canonicalUrl : ''"
        :test-first="true"
        @update:profile-id="newSeries.parser_profile_id = $event"
      />
    </div>

    <template #footer>
      <Button label="Добавить" icon="pi pi-check" :loading="saving" :disabled="!canAddSeries || parsing" @click="addSeries" />
      <Button label="Отмена" icon="pi pi-times" severity="secondary" @click="emit('close')" />
    </template>
  </ModalShell>
</template>
