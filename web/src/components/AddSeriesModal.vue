<script setup lang="ts">
import { ref, reactive, computed, watch, onMounted, onBeforeUnmount } from "vue"
import Button from "primevue/button"
import { useToast } from "primevue/usetoast"
import ModalShell from "./ModalShell.vue"
import ModalTabs from "./ModalTabs.vue"
import SeriesInfoRows from "./SeriesInfoRows.vue"
import SheetField from "./SheetField.vue"
import SheetRow from "./SheetRow.vue"
import TmdbSync from "./TmdbSync.vue"
import { useTmdbChoice } from "../composables/useTmdbChoice"
import StGroup from "./StGroup.vue"
import StInput from "./StInput.vue"
import StLabel from "./StLabel.vue"
import StSelect from "./StSelect.vue"
import SettingsParser from "./settings/SettingsParser.vue"
import AppSearch from "./AppSearch.vue"
import SourcesEditor from "./SourcesEditor.vue"
import { api } from "../api/client"
import { t } from "../i18n"
import { reasonText } from "../lib/reason"
import { joinVkAddress, siteOf, sourceTypeOf, splitVkAddress, titlesOf, vkQuery, type SourceAddress } from "../lib/sources"
import { useApi } from "../composables/useApi"
import { useSSE } from "../composables/useSSE"
import { useSourceDrafts } from "../composables/useSourceDrafts"
import { fetchProfiles, resolveAddress } from "../composables/sourceCalls"
import { compareNames } from "../lib/collate"
import { isAbsolutePath } from "../lib/fsnames"
import type { components } from "../api/schema"

type SeriesCreate = components["schemas"]["SeriesCreate"]

// Add-series window. An address is told by POST /api/sources/resolve: a site reads it, so it is a video source
// and goes into the list; otherwise it is a tracker page (POST /api/parse_url). The series is created by
// POST /api/series together with its sources.
const emit = defineEmits<{ (e: "close"): void; (e: "created"): void }>()
const { request } = useApi()
const toast = useToast()

// --- типы ответов (бэк отдаёт DynamicObject; сужаем под контракт) ---
interface Torrent { torrent_id?: string | number; link?: string; magnet?: string; date_time?: string; episodes?: string; quality?: string }
interface ParseResult { success?: boolean; title?: { ru?: string; en?: string }; torrents?: Torrent[]; tracker_info?: TrackerInfo; error?: string }
interface TrackerInfo { ui_features?: { quality_selector?: string } }

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
  // The profile of the one torrent source; a video source keeps its own in the list of sources.
  parser_profile_id: null as number | null,
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
// Sources of a video series pile up here until "Add"; a torrent series has the one address of the field above.
const {
  items: sourceItems, bodies: sourceBodies, failed: sourcesFailed, names: sourceNames, error: sourcesError, busy: sourcesBusy,
  add: addDraft, remove: removeDraft, change: changeDraft, reread: rereadDraft, stop: stopDraft,
} = useSourceDrafts()
// The names the page of a source gives fill the fields the user left empty, as the names of a tracker page do.
watch(sourceNames, (names) => {
  const titles = titlesOf(names)
  if (!newSeries.name.trim()) newSeries.name = titles.ru
  if (!newSeries.name_en.trim()) newSeries.name_en = titles.en
})
// The address of the field that a site reads: it becomes a source on the user's word, so a half-typed one makes none.
const candidate = ref("")
// A candidate that is a channel of VK needs its query: the channel is read whole and only the query keeps its videos.
const candidateChannel = ref(false)
const candidateQuery = ref("")
const kind = computed<"video" | "torrent">(() => (sourceItems.value.length ? "video" : "torrent"))
// What the user has typed or chosen and the series is not yet made of: the window asks before it is closed over it.
const dirty = computed(() => !!(newSeries.url || newSeries.name || newSeries.name_en || newSeries.save_path || newSeries.quality_override
  || newSeries.resolution_override || candidate.value || sourceItems.value.length || tmdb.selected.value))
// The test of the rules offers the addresses and the queries of the sources the series is made of, as they stand now.
const draftAddresses = async (): Promise<SourceAddress[]> =>
  sourceItems.value.map((item) => ({ url: item.url, vk: item.sourceType === "vk_video" }))
// How the first source is added: by an address (a page of a site or of a tracker), or by a card a video application found.
const firstMode = ref<"address" | "apps">("address")
const trackerInfo = ref<TrackerInfo | null>(null)
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
const tmdb = useTmdbChoice({ query: () => newSeries.name || newSeries.name_en, season: () => newSeries.season, seasonless: () => isSeasonless.value })

// сортировка ключей групп эпизодов astar (порт sortEpisodeKeys из index.html)
function sortEpisodeKeys(keys: string[]): string[] {
  const firstNum = (k: string) => {
    const m = k.match(/\d+/)
    return m ? parseInt(m[0], 10) : Infinity
  }
  return [...keys].sort((a, b) => {
    const na = firstNum(a)
    const nb = firstNum(b)
    return na === nb ? compareNames(a, b) : na - nb
  })
}

// --- computed ---
const shouldShowValidation = computed(() => {
  if (showValidation.value) return true
  return kind.value === "video" || parsed.value
})
const isSeasonValid = computed(() => {
  if (isSeasonless.value || kind.value === "video") return true
  return /^s\d{2}$/.test(newSeries.season.trim())
})
const canAddSeries = computed(() => {
  const base = !!newSeries.name && !!newSeries.name_en && isAbsolutePath(newSeries.save_path)
  if (kind.value === "video") return base && sourceItems.value.length > 0 && !sourcesFailed.value
  return parsed.value && base && isSeasonValid.value
})
const sortedQualityOptionsKeys = computed(() => {
  if (!site.value.includes("astar")) return []
  return sortEpisodeKeys(Object.keys(episodeQualityOptions))
})
// The names the search of the video applications offers: the original one of the TMDB card chosen, then those typed.
const seriesNames = computed(() => [
  ...(tmdb.selected.value?.original_name ? [{ text: tmdb.selected.value.original_name, mark: t("sources.apps.tmdb") }] : []),
  ...[...(newSeries.name || "").split("/"), newSeries.name_en || ""].map((text) => ({ text })),
].map((name) => ({ ...name, text: name.text.trim() })).filter((name) => name.text))

// --- состояния валидации для рамок групп (valid/invalid/null) ---
type VState = "valid" | "invalid" | null
function vstate(ok: boolean): VState {
  return ok ? "valid" : "invalid"
}
const urlState = computed<VState>(() => {
  if (!shouldShowValidation.value) return null
  if (urlError.value) return "invalid"
  return vstate(parsed.value)
})
const infoStates = computed(() => ({ name: nameState.value, name_en: nameEnState.value, save_path: savePathState.value, season: seasonState.value }))
const addTabs = [
  { label: t("add.tabAdd"), value: "add", icon: "pi-plus-circle" },
  { label: t("add.tabNaming"), value: "naming", icon: "pi-filter" },
] as const
const profileState = computed<VState>(() => (shouldShowValidation.value ? vstate(!!newSeries.parser_profile_id) : null))
const nameState = computed<VState>(() => (shouldShowValidation.value ? vstate(!!newSeries.name) : null))
const nameEnState = computed<VState>(() => (shouldShowValidation.value ? vstate(!!newSeries.name_en) : null))
const savePathState = computed<VState>(() => (shouldShowValidation.value ? vstate(isAbsolutePath(newSeries.save_path)) : null))
const seasonState = computed<VState>(() => {
  if (!shouldShowValidation.value || isSeasonless.value || kind.value === "video") return null
  return vstate(isSeasonValid.value)
})

const profileOptions = computed(() => [
  { label: t("add.profilePick"), value: null as number | null },
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
  () => kind.value === "video" || (!!site.value && (site.value.includes("kinozal") || site.value.includes("rutracker"))),
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
  const out: string[] = []
  for (const id of selectedTorrentIds.value) {
    const r = previewResults.value[id]
    if (r?.files?.length) out.push(...r.files.map((f) => baseName(f.path)))
  }
  return out
})

// --- распознавание URL (debounce 500мс) ---
let debounceTimer: ReturnType<typeof setTimeout> | null = null
watch(() => newSeries.url, () => {
  if (debounceTimer) clearTimeout(debounceTimer)
  debounceTimer = setTimeout(handleUrlInput, 500)
})

async function handleUrlInput() {
  urlError.value = ""
  parsed.value = false
  candidate.value = ""
  candidateChannel.value = false
  newSeries.resolution_override = ""
  const typed = newSeries.url.trim()
  if (!typed) return
  parsing.value = true
  const answer = await resolveAddress(typed)
  parsing.value = false
  if (!answer.ok) {
    urlError.value = answer.text
    return
  }
  if (sourceTypeOf(answer.data) !== null) {
    candidate.value = typed
    candidateChannel.value = sourceTypeOf(answer.data) === "vk_video"
    // A pasted `channel|query` or a channel link with `?q=` gives its query to the field of the query.
    const pasted = candidateChannel.value ? splitVkAddress(typed).query : ""
    if (pasted && !candidateQuery.value.trim()) candidateQuery.value = pasted
    return
  }
  await parseTorrentUrl()
}

// The source goes into the list on the user's word; the field is free again for a tracker page or another source.
async function addSource(address: string): Promise<boolean> {
  const outcome = await addDraft(address)
  if (outcome.kind === "added") return true
  sourcesError.value = outcome.kind === "refused" ? outcome.text : t("sources.add.notSite")
  return false
}

async function addCandidate() {
  const address = candidateChannel.value
    ? joinVkAddress(splitVkAddress(candidate.value).channel, candidateQuery.value)
    : candidate.value
  if (!(await addSource(address))) {
    urlError.value = sourcesError.value
    return
  }
  candidate.value = ""
  candidateChannel.value = false
  candidateQuery.value = ""
  newSeries.url = ""
}

async function parseTorrentUrl() {
  parsing.value = true
  try {
    site.value = new URL(newSeries.url).hostname.replace(/^www\./, "")
  } catch {
    urlError.value = t("add.error.url")
    parsing.value = false
    return
  }
  try {
    const { data, error } = await api.POST("/api/parse_url", { body: { url: newSeries.url } })
    const res = (data ?? null) as ParseResult | null
    if (error || !res) {
      const err = (error ?? null) as { error?: string } | null
      throw new Error(reasonText(err?.error) || t("add.error.parse"))
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
    void prefetchNamingFiles(torrents) // file lists for the naming tab arrive later by events
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
  await api.POST("/api/preview/naming/fetch", { body: { items } })
}

// --- TMDB ---
watch(() => newSeries.name, (v) => {
  if (!tmdb.query.value && !tmdb.selected.value) tmdb.query.value = v
})

// --- сохранение ---
async function addSeries() {
  showValidation.value = true
  if (!canAddSeries.value) {
    toast.add({ severity: "error", summary: t("common.checkFields"), detail: t("add.fillFields"), life: 4000 })
    return
  }
  saving.value = true
  try {
    // The body is the one the contract declares (SeriesCreate): the site and the sources are told by the kind of the series below.
    const payload: SeriesCreate = {
      name: newSeries.name,
      name_en: newSeries.name_en,
      site: "",
      sources: [],
      save_path: newSeries.save_path,
      season: isSeasonless.value ? "" : newSeries.season,
      // пустой ручной ввод → null (как в edit-модалке), а не "" в БД
      quality_override: newSeries.quality_override || null,
      resolution_override: newSeries.resolution_override || null,
    }
    if (tmdb.selected.value) {
      payload.tmdb_data = {
        tmdb_id: tmdb.selected.value.id,
        tmdb_season_number: tmdb.seasonNumber.value,
        // «несколько сезонов»: число серий неизвестно до нейминга/скана —
        // не задаём (0), карточка не покажет, агрегатор посчитает позже.
        total_episodes: isSeasonless.value ? 0 : tmdb.episodeCount.value,
        poster_path: tmdb.selected.value.poster_path,
        series_name: tmdb.name(tmdb.selected.value),
        year: tmdb.selected.value.year,
      }
    }
    if (kind.value === "video") {
      payload.site = siteOf(sourceItems.value[0].url)
      payload.sources = sourceBodies.value
    } else {
      payload.site = site.value
      payload.sources = [{ url: newSeries.url, source_type: "torrent", parser_profile_id: newSeries.parser_profile_id }]
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
      payload.torrents = (parserData.value?.torrents ?? []).map((each) => ({ ...each }))
    }
    const ok = await request(api.POST("/api/series", { body: payload }), {
      errorMessage: t("add.createFailed"),
    })
    if (ok !== null) {
      toast.add({ severity: "success", summary: t("common.done"), detail: t("add.created"), life: 3000 })
      emit("created")
      emit("close")
    }
  } finally {
    saving.value = false
  }
}

onMounted(() => {
  void fetchProfiles().then((list) => { profiles.value = list })
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
  <ModalShell size="xl" :dirty="dirty" @close="emit('close')">
    <template #title><i class="pi pi-plus"></i> {{ t("add.title") }}</template>
    <template #header-extra>
      <ModalTabs v-model="activeTab" :tabs="addTabs" />
    </template>

    <div v-show="activeTab === 'add'">
    <!-- URL -->
    <div v-if="kind === 'torrent'" class="field-group add-url-row stack">
      <div class="st-tabs st-tabs-wrap source-mode" role="tablist" :aria-label="t('sources.mode.label')">
        <button
          type="button" class="st-tab" role="tab" :class="{ active: firstMode === 'address' }"
          :aria-selected="firstMode === 'address'" @click="firstMode = 'address'"
        >
          <i class="pi pi-link tab-icon" /><span>{{ t("sources.mode.address") }}</span>
        </button>
        <button
          type="button" class="st-tab" role="tab" :class="{ active: firstMode === 'apps' }"
          :aria-selected="firstMode === 'apps'" @click="firstMode = 'apps'"
        >
          <i class="pi pi-search tab-icon" /><span>{{ t("sources.mode.apps") }}</span>
        </button>
      </div>
      <AppSearch v-if="firstMode === 'apps'" :names="seriesNames" :added="{}" :busy="sourcesBusy" @add="addSource" />
      <!-- A channel of VK stays one field: its address and its query are the two parts of it. -->
      <StGroup v-if="firstMode === 'address'" :state="urlState">
        <StLabel icon="pi pi-link" :text="t('add.urlLabel')" />
        <StInput v-model="newSeries.url" :aria-label="t('add.urlLabel')" />
        <template v-if="candidate && candidateChannel">
          <StLabel icon="pi pi-search" :text="t('sources.vk.query')" />
          <StInput
            v-model="candidateQuery" class="source-query" :placeholder="t('sources.vk.queryHint')" :aria-label="t('sources.vk.query')"
            @keyup.enter="vkQuery(candidateQuery) && addCandidate()"
          />
        </template>
        <div v-if="candidate" class="item-button-group">
          <button
            type="button" class="btn-icon btn-add btn-text-icon" :disabled="candidateChannel && !vkQuery(candidateQuery)"
            @click="addCandidate"
          >
            <i class="pi pi-plus" /><span>{{ t("sources.add.button") }}</span>
          </button>
        </div>
      </StGroup>
      <div v-if="urlError" class="add-error">{{ urlError }}</div>
      <div v-if="parsing" class="add-hint">{{ t("add.recognizing") }}</div>
      <div v-if="candidate && candidateChannel" class="add-hint">{{ t("sources.vk.hint") }}</div>
    </div>

    <div v-if="parsed || kind === 'video'" class="sheet">
      <!-- Информация о сериале -->
      <SeriesInfoRows
        v-model:seasonless="isSeasonless" :form="newSeries" :states="infoStates" :catalog-name="tmdb.catalogName.value"
        :can-switch-seasons="showSeasonlessSwitch"
      >
        <SheetField v-if="kind === 'torrent'" :label="t('sources.profile.label')" icon="pi pi-filter" :state="profileState" wide>
          <StSelect v-model="newSeries.parser_profile_id" :options="profileOptions" :placeholder="t('add.profilePick')" />
        </SheetField>
      </SeriesInfoRows>

      <!-- Sources of a video series: a group per source, its players and voice-overs -->
      <SheetRow v-if="kind === 'video'" :caption="t('sources.title')">
        <SourcesEditor
          :items="sourceItems" :profiles="profiles" :error="sourcesError" :busy="sourcesBusy" :adder="addSource"
          :names="seriesNames"
          @remove="removeDraft" @change="changeDraft" @reread="rereadDraft" @stop="stopDraft"
        />
      </SheetRow>

      <!-- Качество -->
      <SheetRow v-if="showQualityBlock" :caption="t('seriesForm.qualityChoice')">
        <template v-if="qualitySelector === 'anilibria'">
          <SheetField v-if="isQualityOptionsReady && qualityOptionsAnilibria.length" :label="t('common.quality')" icon="pi pi-video">
            <StSelect
              :model-value="newSeries.qualityByEpisodes.all"
              :options="qualityOptionsAnilibria"
              @update:model-value="newSeries.qualityByEpisodes.all = $event as string"
            />
          </SheetField>
          <p v-else-if="isQualityOptionsReady" class="sheet-problem">{{ t("seriesForm.qualityNotFound") }}</p>
        </template>

        <template v-if="qualitySelector === 'astar' && isQualityOptionsReady">
          <p class="sheet-hint">{{ t("add.astarHint") }}</p>
          <template v-for="ep in sortedQualityOptionsKeys" :key="ep">
            <SheetField v-if="episodeQualityOptions[ep].length > 1" :label="t('seriesForm.episodeGroup', { ep })" icon="pi pi-video">
              <StSelect
                :model-value="newSeries.qualityByEpisodes[ep]"
                :options="episodeQualityOptions[ep].map((q) => ({ label: q, value: q }))"
                @update:model-value="newSeries.qualityByEpisodes[ep] = $event as string"
              />
            </SheetField>
          </template>
          <p v-if="!astarHasAlternatives" class="sheet-hint">{{ t("seriesForm.noAlternatives") }}</p>
        </template>
      </SheetRow>

      <TmdbSync :tmdb="tmdb" :seasonless="isSeasonless" />

      <!-- Доступные торренты -->
      <SheetRow v-if="kind === 'torrent' && parserData && parserData.torrents.length" :caption="t('add.torrentsTitle', { count: parserData.torrents.length })">
        <div class="div-table table-site-torrents">
          <div class="div-table-header">
            <div class="div-table-cell">ID</div>
            <div class="div-table-cell">{{ t("common.link") }}</div>
            <div class="div-table-cell">{{ t("common.date") }}</div>
            <div class="div-table-cell">{{ t("common.episodes") }}</div>
            <div class="div-table-cell">{{ t("common.quality") }}</div>
          </div>
          <div class="div-table-body">
            <div v-for="(tor, i) in parserData.torrents" :key="tor.torrent_id ?? i" class="div-table-row">
              <div class="div-table-cell">{{ tor.torrent_id }}</div>
              <div class="div-table-cell">{{ tor.link }}</div>
              <div class="div-table-cell">{{ tor.date_time }}</div>
              <div class="div-table-cell">{{ tor.episodes }}</div>
              <div class="div-table-cell">{{ tor.quality }}</div>
            </div>
          </div>
        </div>
      </SheetRow>
    </div>
    </div>

    <div v-show="activeTab === 'naming'">
      <SettingsParser
        :initial-profile-id="newSeries.parser_profile_id"
        :test-initial-titles="namingTitles"
        :test-initial-url="kind === 'video' ? sourceItems[0].url : ''"
        :test-first="true"
        :load-sources="kind === 'video' ? draftAddresses : undefined"
        @update:profile-id="kind === 'torrent' && (newSeries.parser_profile_id = $event)"
      />
    </div>

    <template #footer>
      <Button :label="t('common.add')" icon="pi pi-check" :loading="saving" :disabled="!canAddSeries || parsing" @click="addSeries" />
      <Button :label="t('common.cancel')" icon="pi pi-times" severity="secondary" @click="emit('close')" />
    </template>
  </ModalShell>
</template>
