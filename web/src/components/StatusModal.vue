<script setup lang="ts">
import { ref, computed, onMounted, watch } from "vue"
import Button from "primevue/button"
import ModalShell from "./ModalShell.vue"
import StatusProperties from "./status/StatusProperties.vue"
import StatusOutput from "./status/StatusOutput.vue"
import SettingsParser from "./settings/SettingsParser.vue"
import AgentsPanel from "./settings/AgentsPanel.vue"
import { api } from "../api/client"
import { useApi } from "../composables/useApi"
import { fetchSources } from "../composables/sourceCalls"
import type { SourceAddress } from "../lib/sources"
import { useSeriesStore, type Series } from "../stores/series"
import { t } from "../i18n"
import LoadingMark from "./LoadingMark.vue"

// Status window: the shell of the settings window (ModalShell xl, fixed height) with segment tabs (st-tabs), the same
// for every series whatever its sources. The footer has "Save" on the Properties tab only. The tab of the rules is the
// configurator of the profiles of the rules, at hand from the series.
const props = defineProps<{ seriesId: number }>()
const emit = defineEmits<{ (e: "close"): void; (e: "updated"): void }>()
const { request } = useApi()

const series = ref<Series | null>(null)
const propsRef = ref<InstanceType<typeof StatusProperties> | null>(null)
const saving = ref(false)
// Занятость берём из стора: он живёт на SSE, а локальная копия серии
// читается один раз и застыла бы на «Обработка…» после сохранения.
const seriesStore = useSeriesStore()
const syncing = computed(() => !!(seriesStore.byId(props.seriesId)?.is_busy ?? series.value?.is_busy))
const isBusy = computed(() => saving.value || syncing.value)
const saveLabel = computed(() =>
  saving.value ? t("common.saving") : syncing.value ? t("status.syncing") : t("common.save"))

type TabKey = "properties" | "composition" | "parser" | "agents"
const tab = ref<TabKey>("properties")
// A tab is built when it is first shown and kept from then on: a window opened on one tab neither builds the others
// (the output of a long series is thousands of elements) nor asks the core for what they show.
const built = ref(new Set<TabKey>([tab.value]))
const tabs = computed(() => [
  { value: "properties" as TabKey, label: t("status.tab.properties"), icon: "pi-info-circle" },
  { value: "composition" as TabKey, label: t("output.tab"), icon: "pi-sitemap" },
  { value: "parser" as TabKey, label: t("rules.tab"), icon: "pi-filter" },
  { value: "agents" as TabKey, label: t("status.tab.agents"), icon: "pi-server" },
])

async function load() {
  const data = (await request(
    api.GET("/api/series/{series_id}", { params: { path: { series_id: props.seriesId } } }),
    { errorMessage: t("status.notFound") },
  )) as Series | null
  if (data) series.value = data
}

// The sources of the series as the core has them now: the test of the rules offers their addresses and queries to paste.
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

watch(tab, (next) => {
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
      <StatusOutput
        v-if="built.has('composition')"
        v-show="tab === 'composition'"
        :series-id="seriesId"
        :series-name="String(series.name ?? '')"
      />
      <SettingsParser
        v-if="built.has('parser')"
        v-show="tab === 'parser'"
        :load-sources="sourceAddresses"
      />
      <AgentsPanel v-if="built.has('agents')" v-show="tab === 'agents'" :series-id="seriesId" />
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

