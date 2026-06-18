<script setup lang="ts">
import { ref, computed, onMounted } from "vue"
import Button from "primevue/button"
import ModalShell from "./ModalShell.vue"
import StatusProperties from "./status/StatusProperties.vue"
import StatusComposition from "./status/StatusComposition.vue"
import { api } from "../api/client"
import { useApi } from "../composables/useApi"
import type { Series } from "../stores/series"

// Окно «Статус сериала» — та же оболочка, что у Настроек (ModalShell xl,
// фикс-высота) + вкладки-сегмент (st-tabs). Вкладки зависят от source_type.
// Футер: «Сохранить» только на «Свойствах». Composition/History/Slicing —
// следующие шаги (заглушки).
const props = defineProps<{ seriesId: number }>()
const emit = defineEmits<{ (e: "close"): void; (e: "updated"): void }>()
const { request } = useApi()

const series = ref<Series | null>(null)
const propsRef = ref<InstanceType<typeof StatusProperties> | null>(null)
const saving = ref(false)
const isBusy = computed(() => saving.value || !!series.value?.is_busy)
const isVk = computed(() => series.value?.source_type === "vk_video")

type TabKey = "properties" | "composition" | "slicing" | "history"
const tab = ref<TabKey>("properties")
const tabs = computed(() => {
  const t = [{ value: "properties" as TabKey, label: "Свойства", icon: "pi-info-circle" }]
  t.push({ value: "composition", label: "Композиция", icon: "pi-sitemap" })
  if (isVk.value) t.push({ value: "slicing", label: "Нарезка", icon: "pi-images" })
  t.push({ value: "history", label: "История", icon: "pi-history" })
  return t
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
        v-if="!isVk"
        v-show="tab === 'composition'"
        :series-id="seriesId"
        :series-name="String(series.name ?? '')"
      />
      <div v-else v-show="tab === 'composition'" class="status-stub">Композиция VK — следующий шаг переноса.</div>
      <div v-if="isVk" v-show="tab === 'slicing'" class="status-stub">Вкладка «Нарезка» — следующий шаг переноса.</div>
      <div v-show="tab === 'history'" class="status-stub">Вкладка «История» — следующий шаг переноса.</div>
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

<style scoped>
.status-stub { padding: 40px; text-align: center; color: var(--text-muted); }
</style>
