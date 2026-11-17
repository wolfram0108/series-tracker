<script setup lang="ts">
import { ref, reactive, computed, watch, onMounted, onBeforeUnmount } from "vue"
import StTab from "./StTab.vue"
import Button from "primevue/button"
import { useToast } from "primevue/usetoast"
import ModalShell from "./ModalShell.vue"
import ModalTabs from "./ModalTabs.vue"
import SeriesInfoRows from "./SeriesInfoRows.vue"
import SheetRow from "./SheetRow.vue"
import TmdbSync from "./TmdbSync.vue"
import { useTmdbChoice } from "../composables/useTmdbChoice"
import StGroup from "./StGroup.vue"
import StInput from "./StInput.vue"
import StLabel from "./StLabel.vue"
import SettingsParser from "./settings/SettingsParser.vue"
import AppSearch from "./AppSearch.vue"
import SourcesEditor from "./SourcesEditor.vue"
import { api } from "../api/client"
import { t } from "../i18n"
import { joinVkAddress, siteOf, sourceTypeOf, splitVkAddress, titlesOf, vkQuery, type SourceAddress } from "../lib/sources"
import { useApi } from "../composables/useApi"
import { useSourceDrafts } from "../composables/useSourceDrafts"
import { fetchProfiles, resolveAddress } from "../composables/sourceCalls"
import { isAbsolutePath } from "../lib/fsnames"
import type { components } from "../api/schema"

type SeriesCreate = components["schemas"]["SeriesCreate"]

// Add-series window. An address is told by POST /api/sources/resolve: the site that reads it says which source it
// makes (a page of a site, a channel of VK, a tracker page), and the source goes into the list. The series is created
// by POST /api/series together with its sources.
const emit = defineEmits<{ (e: "close"): void; (e: "created"): void }>()
const { request } = useApi()
const toast = useToast()

// --- состояние формы ---
const newSeries = reactive({
  url: "",
  save_path: "",
  name: "",
  name_en: "",
  season: "s01",
  quality_override: "",
  resolution_override: "",
})
const isSeasonless = ref(false)
const urlError = ref("")
const parsing = ref(false)
const showValidation = ref(false)
// The sources of the series pile up here until "Add".
const {
  items: sourceItems, bodies: sourceBodies, failed: sourcesFailed, names: sourceNames, error: sourcesError, busy: sourcesBusy,
  add: addDraft, remove: removeDraft, change: changeDraft, reread: rereadDraft, stop: stopDraft,
} = useSourceDrafts()
// The names the page of a source gives fill the fields the user left empty.
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
// What the user has typed or chosen and the series is not yet made of: the window asks before it is closed over it.
const dirty = computed(() => !!(newSeries.url || newSeries.name || newSeries.name_en || newSeries.save_path || newSeries.quality_override
  || newSeries.resolution_override || candidate.value || sourceItems.value.length || tmdb.selected.value))
// The test of the rules offers the addresses and the queries of the sources the series is made of, as they stand now.
const draftAddresses = async (): Promise<SourceAddress[]> =>
  sourceItems.value.map((item) => ({ url: item.url, vk: item.sourceType === "vk_video" }))
// How the first source is added: by an address (a page of a site or of a tracker), or by a card a video application found.
const firstMode = ref<"address" | "apps">("address")
const profiles = ref<{ id: number; name: string }[]>([])
const saving = ref(false)

// The tabs of the window: the form, and the section of the rules (the check of the naming).
const activeTab = ref<"add" | "naming">("add")

// --- TMDB ---
const tmdb = useTmdbChoice({ query: () => newSeries.name || newSeries.name_en, season: () => newSeries.season, seasonless: () => isSeasonless.value })

// --- computed ---
const shouldShowValidation = computed(() => showValidation.value || sourceItems.value.length > 0)
const canAddSeries = computed(() => !!newSeries.name && !!newSeries.name_en && isAbsolutePath(newSeries.save_path)
  && sourceItems.value.length > 0 && !sourcesFailed.value)
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
const urlState = computed<VState>(() => (urlError.value ? "invalid" : null))
const infoStates = computed(() => ({ name: nameState.value, name_en: nameEnState.value, save_path: savePathState.value, season: seasonState.value }))
const addTabs = [
  { label: t("add.tabAdd"), value: "add", icon: "pi-plus-circle" },
  { label: t("add.tabNaming"), value: "naming", icon: "pi-filter" },
] as const
const nameState = computed<VState>(() => (shouldShowValidation.value ? vstate(!!newSeries.name) : null))
const nameEnState = computed<VState>(() => (shouldShowValidation.value ? vstate(!!newSeries.name_en) : null))
const savePathState = computed<VState>(() => (shouldShowValidation.value ? vstate(isAbsolutePath(newSeries.save_path)) : null))
// The season of the card fills in what a row does not say; any text of it is taken.
const seasonState = computed<VState>(() => null)
// --- распознавание URL (debounce 500мс) ---
let debounceTimer: ReturnType<typeof setTimeout> | null = null
watch(() => newSeries.url, () => {
  if (debounceTimer) clearTimeout(debounceTimer)
  debounceTimer = setTimeout(handleUrlInput, 500)
})

async function handleUrlInput() {
  urlError.value = ""
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
  urlError.value = t("sources.add.notSite")
}

// The source goes into the list on the user's word; the field is free again for another source.
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
    // The body is the one the contract declares (SeriesCreate): the site is the one of the first source.
    const payload: SeriesCreate = {
      name: newSeries.name,
      name_en: newSeries.name_en,
      site: siteOf(sourceItems.value[0].url),
      sources: sourceBodies.value,
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
})
onBeforeUnmount(() => {
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
    <!-- The first source; the next ones are added in the list of sources -->
    <div v-if="!sourceItems.length" class="field-group add-url-row stack">
      <div class="st-tabs st-tabs-wrap source-mode" role="tablist" :aria-label="t('sources.mode.label')">
        <StTab :active="firstMode === 'address'" :label="t('sources.mode.address')" @click="firstMode = 'address'">
          <template #before><i class="pi pi-link tab-icon" /></template>
        </StTab>
        <StTab :active="firstMode === 'apps'" :label="t('sources.mode.apps')" @click="firstMode = 'apps'">
          <template #before><i class="pi pi-search tab-icon" /></template>
        </StTab>
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

    <div v-if="sourceItems.length" class="sheet">
      <!-- Информация о сериале -->
      <SeriesInfoRows
        v-model:seasonless="isSeasonless" :form="newSeries" :states="infoStates" :catalog-name="tmdb.catalogName.value"
        :can-switch-seasons="true"
      />

      <!-- Sources: a group per source, its players and voice-overs -->
      <SheetRow :caption="t('sources.title')">
        <SourcesEditor
          :items="sourceItems" :profiles="profiles" :error="sourcesError" :busy="sourcesBusy" :adder="addSource"
          :names="seriesNames"
          @remove="removeDraft" @change="changeDraft" @reread="rereadDraft" @stop="stopDraft"
        />
      </SheetRow>

      <TmdbSync :tmdb="tmdb" :seasonless="isSeasonless" />

    </div>
    </div>

    <div v-show="activeTab === 'naming'">
      <SettingsParser
        :test-initial-url="sourceItems.length ? sourceItems[0].url : ''"
        :test-first="true"
        :load-sources="draftAddresses"
      />
    </div>

    <template #footer>
      <Button :label="t('common.add')" icon="pi pi-check" :loading="saving" :disabled="!canAddSeries || parsing" @click="addSeries" />
      <Button :label="t('common.cancel')" icon="pi pi-times" severity="secondary" @click="emit('close')" />
    </template>
  </ModalShell>
</template>
