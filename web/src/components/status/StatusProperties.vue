<script setup lang="ts">
import { ref, reactive, computed, onMounted } from "vue"
import { useToast } from "primevue/usetoast"
import SeriesInfoRows from "../SeriesInfoRows.vue"
import SheetRow from "../SheetRow.vue"
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

type VState = "valid" | "invalid" | null

const s = props.series

// --- редактируемые поля (инициализация из серии) ---
const form = reactive({
  name: String(s.name ?? ""),
  name_en: String((s as Record<string, unknown>).name_en ?? ""),
  // A folder a save asked for and the next sync moves to is what the card holds (I-59).
  save_path: String(s.planned_save_path ?? (s as Record<string, unknown>).save_path ?? ""),
  season: String(s.season ?? ""),
  quality_override: String((s as Record<string, unknown>).quality_override ?? ""),
  resolution_override: String((s as Record<string, unknown>).resolution_override ?? ""),
})
// What the user has typed and not saved: the window asks before it is closed over it.
const typed = (): string => JSON.stringify([form.name, form.name_en, form.save_path, form.season, form.quality_override, form.resolution_override])
const saved = ref(typed())
const dirty = computed(() => typed() !== saved.value)
// The sources act at once (their own requests); the profiles are what their groups choose from.
const sources = useSeriesSources(Number(s.id), s.sources ?? [])
useBusyBar(() => sources.busy.value || sources.tablesLoading.value)
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
function vstate(ok: boolean): VState {
  return ok ? "valid" : "invalid"
}
const infoStates = computed(() => ({ name: nameState.value, name_en: nameEnState.value, save_path: savePathState.value, season: seasonState.value }))
const nameState = computed<VState>(() => (showValidation.value ? vstate(!!form.name) : null))
const nameEnState = computed<VState>(() => (showValidation.value ? vstate(!!form.name_en) : null))
const savePathState = computed<VState>(() => (showValidation.value ? vstate(isAbsolutePath(form.save_path)) : null))
// The season of the card fills in what a row does not say; any text of it is taken.
const seasonState = computed<VState>(() => null)

const canSave = computed(() => !!form.name && !!form.name_en && isAbsolutePath(form.save_path))

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

    <!-- Sources: a group per source -->
    <SheetRow :caption="t('sources.title')">
      <SourcesEditor
        :items="sources.items.value" :profiles="profiles" :error="sources.error.value || sources.layoutFailure.value" :busy="sources.busy.value || seriesBusy" :waiting="sources.waiting.value"
        :adder="sources.add" :names="seriesNames" :load-names="tmdbOriginal"
        @remove="sources.remove" @change="sources.change" @reread="sources.reread" @stop="sources.stop"
      />
    </SheetRow>

    <!-- TMDB -->
    <TmdbSync :tmdb="tmdb" :seasonless="isSeasonless" />
  </div>
</template>
