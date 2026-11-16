<script setup lang="ts">
import { ref, computed, onMounted, watch } from "vue"
import Button from "primevue/button"
import ModalShell from "./ModalShell.vue"
import StatusProperties from "./status/StatusProperties.vue"
import StatusComposition from "./status/StatusComposition.vue"
import StatusOutput from "./status/StatusOutput.vue"
import StatusHistory from "./status/StatusHistory.vue"
import SettingsParser from "./settings/SettingsParser.vue"
import AgentsPanel from "./settings/AgentsPanel.vue"
import { api } from "../api/client"
import { useApi } from "../composables/useApi"
import { fetchSources } from "../composables/sourceCalls"
import type { SourceAddress } from "../lib/sources"
import { useSeriesStore, type Series } from "../stores/series"
import { t } from "../i18n"
import LoadingMark from "./LoadingMark.vue"

// Status window: the shell of the settings window (ModalShell xl, fixed height) with segment tabs (st-tabs); the
// tabs depend on the kind of the series. The footer has "Save" on the Properties tab only. The tab of the VK filters is
// the configurator of the profiles of the rules, at hand from the series.
const props = defineProps<{ seriesId: number }>()
const emit = defineEmits<{ (e: "close"): void; (e: "updated"): void }>()
const { request } = useApi()

const series = ref<Series | null>(null)
const propsRef = ref<InstanceType<typeof StatusProperties> | null>(null)
const compRef = ref<InstanceType<typeof StatusComposition> | null>(null)
const saving = ref(false)
// Занятость берём из стора: он живёт на SSE, а локальная копия серии
// читается один раз и застыла бы на «Обработка…» после сохранения.
const seriesStore = useSeriesStore()
const syncing = computed(() => !!(seriesStore.byId(props.seriesId)?.is_busy ?? series.value?.is_busy))
const isBusy = computed(() => saving.value || syncing.value)
const saveLabel = computed(() =>
  saving.value ? t("common.saving") : syncing.value ? t("status.syncing") : t("common.save"))
const isVideo = computed(() => series.value?.kind === "video")

type TabKey = "properties" | "composition" | "parser" | "agents" | "history"
const tab = ref<TabKey>("properties")
// A tab is built when it is first shown and kept from then on: a window opened on one tab neither builds the others
// (the output of a long series is thousands of elements) nor asks the core for what they show.
const built = ref(new Set<TabKey>([tab.value]))
const tabs = computed(() => {
  const list = [{ value: "properties" as TabKey, label: t("status.tab.properties"), icon: "pi-info-circle" }]
  list.push({ value: "composition", label: isVideo.value ? t("output.tab") : t("status.tab.composition"), icon: "pi-sitemap" })
  list.push({ value: "parser", label: t("rules.tab"), icon: "pi-filter" })
  list.push({ value: "agents", label: t("status.tab.agents"), icon: "pi-server" })
  // The history of a torrent series is its releases; a video series has its sources and its plan instead.
  if (!isVideo.value) list.push({ value: "history", label: t("status.tab.history"), icon: "pi-history" })
  return list
})

async function load() {
  const data = (await request(
    api.GET("/api/series/{series_id}", { params: { path: { series_id: props.seriesId } } }),
    { errorMessage: t("status.notFound") },
  )) as Series | null
  if (data) series.value = data
}

// The sources of the series as the core has them now: the test of the rules offers their addresses and queries to paste.
// A torrent series has no page to read in its source, so its test offers nothing.
async function sourceAddresses(): Promise<SourceAddress[]> {
  const listed = await fetchSources(props.seriesId)
  return listed.ok ? listed.data.map((source) => ({ url: source.url, vk: source.source_type === "vk_video" })) : []
}

async function save() {
  if (!propsRef.value) return
  saving.value = true
  try {
    const ok = await propsRef.value.save()
    if (ok) {
      emit("updated")
      await load()
    }
  } finally {
    saving.value = false
  }
}

// Композиция смонтирована один раз (v-show, без ре-монтажа при смене вкладки),
// поэтому сама не освежается. При переходе на вкладку «Композиция»
// перечитываем её — иначе после «Сохранить» (фоновое усыновление меняет БД)
// видны старые данные до закрытия/открытия модалки.
watch(tab, (next) => {
  if (next === "composition" && !isVideo.value) compRef.value?.reload?.()
  built.value = new Set([...built.value, next])
})

onMounted(load)
</script>

<template>
  <ModalShell size="xl" fixed-height :dirty="!!propsRef?.dirty" @close="emit('close')">
    <template #title><i class="pi pi-info-circle"></i> {{ t("status.title") }}</template>
    <template #header-extra>
      <div class="header-tabs st-tabs">
        <button
          v-for="tb in tabs"
          :key="tb.value"
          class="st-tab"
          :class="{ active: tab === tb.value }"
          :title="tb.label"
          @click="tab = tb.value"
        >
          <i class="pi tab-icon" :class="tb.icon"></i>
          <span class="tab-label" :data-text="tb.label"><span class="tl-text">{{ tb.label }}</span></span>
        </button>
      </div>
    </template>

    <div v-if="!series" class="status-loading"><LoadingMark /></div>
    <template v-else>
      <StatusProperties v-show="tab === 'properties'" ref="propsRef" :series="series" @saving="saving = $event" />
      <StatusComposition
        v-if="!isVideo && built.has('composition')"
        ref="compRef"
        v-show="tab === 'composition'"
        :series-id="seriesId"
        :series-name="String(series.name ?? '')"
      />
      <StatusOutput
        v-else-if="isVideo && built.has('composition')"
        v-show="tab === 'composition'"
        :series-id="seriesId"
        :series-name="String(series.name ?? '')"
      />
      <SettingsParser
        v-if="built.has('parser')"
        v-show="tab === 'parser'"
        :load-sources="isVideo ? sourceAddresses : undefined"
      />
      <AgentsPanel v-if="built.has('agents')" v-show="tab === 'agents'" :series-id="seriesId" />
      <StatusHistory v-if="!isVideo && built.has('history')" v-show="tab === 'history'" :series-id="seriesId" />
    </template>

    <template #footer>
      <Button
        v-if="tab === 'properties'"
        :label="saveLabel"
        icon="pi pi-check"
        :loading="saving"
        :disabled="isBusy"
        @click="save"
      />
      <Button :label="t('common.close')" icon="pi pi-times" severity="secondary" @click="emit('close')" />
    </template>
  </ModalShell>
</template>

