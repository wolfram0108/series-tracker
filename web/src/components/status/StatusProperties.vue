<script setup lang="ts">
import { ref, reactive, computed, onMounted } from "vue"
import { useToast } from "primevue/usetoast"
import SeriesInfoRows from "../SeriesInfoRows.vue"
import SheetField from "../SheetField.vue"
import SheetRow from "../SheetRow.vue"
import StSelect from "../StSelect.vue"
import SourcesEditor from "../SourcesEditor.vue"
import TmdbSync from "../TmdbSync.vue"
import { useTmdbChoice } from "../../composables/useTmdbChoice"
import { api } from "../../api/client"
import { useApi } from "../../composables/useApi"
import { fetchProfiles } from "../../composables/sourceCalls"
import { useSeriesSources } from "../../composables/useSeriesSources"
import { t } from "../../i18n"
import { useBusyBar } from "../../composables/useWindowBusy"
import { useSeriesStore, type Series } from "../../stores/series"
import { compareNames } from "../../lib/collate"
import { isAbsolutePath } from "../../lib/fsnames"

// "Properties" tab of the status window. Saving is POST /api/series/{id}; the fields of the sources are not in it:
// a source changes at once by its own request (the "Sources" block). save() is called from the footer of
// StatusModal through a ref and returns the success.
const props = defineProps<{ series: Series }>()
// A series whose sync is under way is busy until every work of the run has ended: nothing here is saved meanwhile.
const seriesStore = useSeriesStore()
const seriesBusy = computed(() => !!(seriesStore.byId(props.series.id)?.is_busy ?? props.series.is_busy))
const emit = defineEmits<{ (e: "saving", v: boolean): void }>()
const { request } = useApi()
const toast = useToast()

interface Torrent { torrent_id?: string | number; link?: string; date_time?: string; episodes?: string; quality?: string; is_active?: boolean }
type VState = "valid" | "invalid" | null

const s = props.series
const isVideo = s.kind === "video"

// --- редактируемые поля (инициализация из серии) ---
const form = reactive({
  name: String(s.name ?? ""),
  name_en: String((s as Record<string, unknown>).name_en ?? ""),
  // A folder a save asked for and the next sync moves to is what the card holds (I-59).
  save_path: String(s.planned_save_path ?? (s as Record<string, unknown>).save_path ?? ""),
  season: String(s.season ?? ""),
  quality: String((s as Record<string, unknown>).quality ?? ""),
  quality_override: String((s as Record<string, unknown>).quality_override ?? ""),
  resolution_override: String((s as Record<string, unknown>).resolution_override ?? ""),
})
// What the user has typed and not saved: the window asks before it is closed over it (the quality a read of the site picks
// for itself is no typing).
const typed = (): string => JSON.stringify([form.name, form.name_en, form.save_path, form.season, form.quality_override, form.resolution_override])
const saved = ref(typed())
const dirty = computed(() => typed() !== saved.value)
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
    return na === nb ? compareNames(a, b) : na - nb
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
      api.GET("/api/series/{series_id}/torrents/history", { params: { path: { series_id: Number(s.id) } } }),
    )) as Torrent[] | null
    if (Array.isArray(hist)) {
      allSiteTorrents.value = hist
      buildQualityOptions(hist)
    }
    const { data } = await api.POST("/api/parse_url", { body: { url: torrentUrl } })
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
// The address of the one source of a torrent series: the tracker page its releases are listed on.
const torrentUrl = s.sources?.[0]?.url ?? ""
// The sources act at once (their own requests); the profiles are what their groups choose from.
const sources = useSeriesSources(Number(s.id), isVideo ? "video" : "torrent", s.sources ?? [])
useBusyBar(() => siteTorrentsLoading.value || sources.busy.value || sources.tablesLoading.value)
const isSeasonless = ref(!form.season)
const showValidation = ref(false)

const profiles = ref<{ id: number; name: string }[]>([])

// --- TMDB (как в Add) ---
const tmdb = useTmdbChoice({ query: () => form.name || form.name_en, season: () => form.season, seasonless: () => isSeasonless.value })

// предзаполнение из сохранённого tmdb_info
const info = (s as Record<string, unknown>).tmdb_info as Record<string, unknown> | null
if (info && info.tmdb_id) {
  tmdb.selected.value = {
    id: Number(info.tmdb_id),
    name: String(info.series_name ?? ""),
    year: info.year ? String(info.year) : undefined,
    poster_path: info.poster_path ? String(info.poster_path) : undefined,
  }
  tmdb.episodeCount.value = Number(info.total_episodes ?? 0)
}

// The names the search of the video applications offers: those of the series, then the original one of TMDB, asked
// only when the search is opened.
const seriesNames = computed(() => [...form.name.split("/"), form.name_en].map((text) => ({ text: text.trim() })).filter((name) => name.text))
async function tmdbOriginal(): Promise<{ text: string; mark?: string }[]> {
  if (!tmdb.selected.value?.id) return []
  const { data } = await api.GET("/api/tmdb/details/{tmdb_id}", { params: { path: { tmdb_id: tmdb.selected.value.id } } })
  const original = String((data as Record<string, unknown> | undefined)?.original_name ?? "").trim()
  return original ? [{ text: original, mark: t("sources.apps.tmdb") }] : []
}

// --- валидация ---
const isSeasonValid = computed(() => {
  if (isSeasonless.value || isVideo) return true
  return /^s\d{2}$/.test(form.season.trim())
})
function vstate(ok: boolean): VState {
  return ok ? "valid" : "invalid"
}
const infoStates = computed(() => ({ name: nameState.value, name_en: nameEnState.value, save_path: savePathState.value, season: seasonState.value }))
const nameState = computed<VState>(() => (showValidation.value ? vstate(!!form.name) : null))
const nameEnState = computed<VState>(() => (showValidation.value ? vstate(!!form.name_en) : null))
const savePathState = computed<VState>(() => (showValidation.value ? vstate(isAbsolutePath(form.save_path)) : null))
const seasonState = computed<VState>(() => {
  if (!showValidation.value || isSeasonless.value || isVideo) return null
  return vstate(isSeasonValid.value)
})

const canSave = computed(() => {
  const base = !!form.name && !!form.name_en && isAbsolutePath(form.save_path)
  if (isVideo) return base
  return base && isSeasonValid.value
})

// вызывается из футера StatusModal; возвращает успех
async function save(): Promise<boolean> {
  showValidation.value = true
  if (!canSave.value) {
    toast.add({ severity: "error", summary: t("common.checkFields"), detail: t("properties.fillRequired"), life: 4000 })
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
    }
    if (!isVideo) {
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
    if (tmdb.selected.value) {
      payload.tmdb_data = {
        tmdb_id: tmdb.selected.value.id,
        tmdb_season_number: tmdb.seasonNumber.value,
        total_episodes: tmdb.episodeCount.value,
        poster_path: tmdb.selected.value.poster_path,
        series_name: tmdb.name(tmdb.selected.value),
        year: tmdb.selected.value.year,
      }
    }
    const ok = await request(
      api.POST("/api/series/{series_id}", { params: { path: { series_id: Number(s.id) } }, body: payload }),
      { errorMessage: t("properties.saveFailed") },
    )
    if (ok !== null) {
      toast.add({ severity: "success", summary: t("properties.saved"), detail: t("properties.savedDetail"), life: 3000 })
      saved.value = typed()
      return true
    }
    return false
  } finally {
    emit("saving", false)
  }
}

defineExpose({ save, dirty })
onMounted(() => {
  void fetchProfiles().then((list) => { profiles.value = list })
  if (!isVideo) void refreshSiteTorrents() // автопарсинг доступных раздач + качество
})
</script>

<template>
  <div class="sheet status-properties">
    <!-- Информация -->
    <SeriesInfoRows
      v-model:seasonless="isSeasonless" :form="form" :states="infoStates" :catalog-name="tmdb.catalogName.value" :can-switch-seasons="true"
    >
      <template #path-note>
        <p v-if="s.planned_save_path" class="relocation-planned">
          {{ t("series.relocation.planned", { path: String(s.save_path ?? "") }) }}
        </p>
      </template>
    </SeriesInfoRows>

    <!-- Sources: a group per source; a torrent series shows the address and the profile of its one source -->
    <SheetRow :caption="t('sources.title')">
      <SourcesEditor
        :items="sources.items.value" :profiles="profiles" :error="sources.error.value || sources.layoutFailure.value" :busy="sources.busy.value || seriesBusy" :waiting="sources.waiting.value"
        :editable="isVideo" :adder="sources.add" :names="seriesNames" :load-names="tmdbOriginal"
        @remove="sources.remove" @change="sources.change" @reread="sources.reread" @stop="sources.stop"
      />
    </SheetRow>

    <!-- Выбор качества (торрент, по tracker_info.ui_features) -->
    <SheetRow v-if="!isVideo && qualitySelector === 'anilibria'" :caption="t('seriesForm.qualityChoice')">
      <SheetField v-if="qualityOptionsAnilibria.length" :label="t('common.quality')" icon="pi pi-video">
        <StSelect
          :model-value="form.quality"
          :options="qualityOptionsAnilibria"
          @update:model-value="form.quality = $event as string"
        />
      </SheetField>
      <p v-else-if="!siteTorrentsLoading" class="sheet-problem">{{ t("seriesForm.qualityNotFound") }}</p>
    </SheetRow>
    <SheetRow v-if="!isVideo && qualitySelector === 'astar'" :caption="t('seriesForm.qualityChoice')">
      <template v-for="ep in sortedQualityOptionsKeys" :key="ep">
        <SheetField v-if="astarQ[ep] && astarQ[ep].length > 1" :label="t('seriesForm.episodeGroup', { ep })" icon="pi pi-video">
          <StSelect
            :model-value="qualityByEpisodes[ep]"
            :options="astarQ[ep].map((q) => ({ label: q, value: q }))"
            @update:model-value="qualityByEpisodes[ep] = $event as string"
          />
        </SheetField>
      </template>
      <p v-if="!siteTorrentsLoading && !astarHasAlternatives" class="sheet-hint">
        {{ t("seriesForm.noAlternatives") }}
      </p>
    </SheetRow>

    <!-- Доступные раздачи с сайта (торрент) -->
    <SheetRow v-if="!isVideo" :caption="t('properties.siteTorrentsTitle')">
      <div v-if="siteTorrentsLoading" class="status-loading">
        <i class="pi pi-spin pi-spinner" /> {{ t("properties.siteTorrentsLoading") }}
      </div>
      <template v-else>
        <div class="div-table table-site-torrents">
          <div class="div-table-header">
            <div class="div-table-cell">ID</div>
            <div class="div-table-cell">{{ t("common.link") }}</div>
            <div class="div-table-cell">{{ t("common.date") }}</div>
            <div class="div-table-cell">{{ t("common.episodes") }}</div>
            <div class="div-table-cell">{{ t("common.quality") }}</div>
          </div>
          <div class="div-table-body">
            <div v-for="(tor, i) in filteredSiteTorrents" :key="tor.torrent_id ?? i" class="div-table-row" :class="{ 'row-stale': siteDataIsStale }">
              <div class="div-table-cell">{{ tor.torrent_id }}</div>
              <div class="div-table-cell">{{ tor.link }}</div>
              <div class="div-table-cell">{{ tor.date_time }}</div>
              <div class="div-table-cell">{{ tor.episodes }}</div>
              <div class="div-table-cell">{{ tor.quality }}</div>
            </div>
            <div v-if="!filteredSiteTorrents.length" class="div-table-row"><div class="div-table-cell span-all">{{ t("properties.torrentsNone") }}</div></div>
          </div>
        </div>
        <p v-if="siteDataIsStale" class="sheet-problem">{{ t("properties.siteStale") }}</p>
      </template>
    </SheetRow>

    <!-- TMDB -->
    <TmdbSync :tmdb="tmdb" :seasonless="isSeasonless" />
  </div>
</template>
