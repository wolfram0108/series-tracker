<script setup lang="ts">
import { ref, computed, onMounted, watch } from "vue"
import Button from "primevue/button"
import ModalShell from "./ModalShell.vue"
import StatusProperties from "./status/StatusProperties.vue"
import StatusComposition from "./status/StatusComposition.vue"
import StatusOutput from "./status/StatusOutput.vue"
import StatusSlicing from "./status/StatusSlicing.vue"
import StatusHistory from "./status/StatusHistory.vue"
import SettingsParser from "./settings/SettingsParser.vue"
import { api } from "../api/client"
import { useApi } from "../composables/useApi"
import { useSeriesStore, type Series } from "../stores/series"
import { t } from "../i18n"

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
const isBusy = computed(
  () => saving.value || !!(seriesStore.byId(props.seriesId)?.is_busy ?? series.value?.is_busy),
)
const isVideo = computed(() => series.value?.kind === "video")

type TabKey = "properties" | "composition" | "slicing" | "parser" | "history"
const tab = ref<TabKey>("properties")
const tabs = computed(() => {
  const list = [{ value: "properties" as TabKey, label: "Свойства", icon: "pi-info-circle" }]
  list.push({ value: "composition", label: isVideo.value ? t("output.tab") : "Композиция", icon: "pi-sitemap" })
  if (isVideo.value) list.push({ value: "slicing", label: "Нарезка", icon: "pi-images" })
  list.push({ value: "parser", label: t("status.tab.parser"), icon: "pi-filter" })
  list.push({ value: "history", label: "История", icon: "pi-history" })
  return list
})

async function load() {
  const data = (await request(
    api.GET("/api/series/{series_id}", { params: { path: { series_id: props.seriesId } } } as never),
    { errorMessage: "Сериал не найден" },
  )) as Series | null
  if (data) series.value = data
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
})

onMounted(load)
</script>

<template>
  <ModalShell size="xl" fixed-height @close="emit('close')">
    <template #title><i class="pi pi-info-circle"></i> Статус</template>
    <template #header-extra>
      <div class="header-tabs st-tabs">
        <button
          v-for="t in tabs"
          :key="t.value"
          class="st-tab"
          :class="{ active: tab === t.value }"
          :title="t.label"
          @click="tab = t.value"
        >
          <i class="pi tab-icon" :class="t.icon"></i>
          <span class="tab-label" :data-text="t.label"><span class="tl-text">{{ t.label }}</span></span>
        </button>
      </div>
    </template>

    <div v-if="!series" class="status-loading"><i class="pi pi-spin pi-spinner" style="font-size: 1.6rem"></i></div>
    <template v-else>
      <StatusProperties v-show="tab === 'properties'" ref="propsRef" :series="series" @saving="saving = $event" />
      <StatusComposition
        v-if="!isVideo"
        ref="compRef"
        v-show="tab === 'composition'"
        :series-id="seriesId"
        :series-name="String(series.name ?? '')"
      />
      <StatusOutput
        v-else
        v-show="tab === 'composition'"
        :series-id="seriesId"
        :series-name="String(series.name ?? '')"
      />
      <StatusSlicing
        v-if="isVideo"
        v-show="tab === 'slicing'"
        :series-id="seriesId"
        :series-name="String(series.name ?? '')"
      />
      <SettingsParser v-if="tab === 'parser'" />
      <StatusHistory v-show="tab === 'history'" :series-id="seriesId" :kind="series.kind ?? 'torrent'" />
    </template>

    <template #footer>
      <Button
        v-if="tab === 'properties'"
        :label="isBusy ? 'Обработка…' : 'Сохранить'"
        icon="pi pi-check"
        :loading="saving"
        :disabled="isBusy"
        @click="save"
      />
      <Button label="Закрыть" icon="pi pi-times" severity="secondary" @click="emit('close')" />
    </template>
  </ModalShell>
</template>

