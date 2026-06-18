<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from "vue"
import Button from "primevue/button"
import StatusProperties from "./status/StatusProperties.vue"
import { api } from "../api/client"
import { useApi } from "../composables/useApi"
import type { Series } from "../stores/series"

// Окно «Статус сериала» — каркас (Шаг 1): шапка с вкладками-папками
// (зависят от source_type), тело активной вкладки, футер (Сохранить —
// только на «Свойствах»). Грузит GET /api/series/{id}. Вкладки
// Композиция/История — следующие шаги, пока заглушки.
const props = defineProps<{ seriesId: number }>()
const emit = defineEmits<{ (e: "close"): void; (e: "updated"): void }>()
const { request } = useApi()

const series = ref<Series | null>(null)
const activeTab = ref<"properties" | "composition" | "history">("properties")
const propsRef = ref<InstanceType<typeof StatusProperties> | null>(null)
const saving = ref(false)

const isBusy = computed(() => saving.value || !!series.value?.is_busy)

interface Tab { key: "properties" | "composition" | "history"; label: string; icon: string }
const tabs = computed<Tab[]>(() => {
  const t: Tab[] = [{ key: "properties", label: "Свойства", icon: "pi pi-info-circle" }]
  t.push({ key: "composition", label: "Композиция", icon: "pi pi-sitemap" })
  t.push({ key: "history", label: "История", icon: "pi pi-history" })
  return t
})

function setTab(key: Tab["key"]) {
  if (isBusy.value && key !== "properties") return
  activeTab.value = key
}

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
      await load() // перечитать актуальные значения
    }
  } finally {
    saving.value = false
  }
}

function onKey(e: KeyboardEvent) {
  if (e.key === "Escape") emit("close")
}
onMounted(() => {
  document.addEventListener("keydown", onKey)
  void load()
})
onUnmounted(() => document.removeEventListener("keydown", onKey))
</script>

<template>
  <div class="modal-overlay" @click.self="emit('close')">
    <div class="modern-modal modal-xl modal-fixed status-modal">
      <div class="status-header">
        <h5 class="modal-title"><i class="pi pi-info-circle" /> Статус</h5>
        <div class="status-tabs">
          <button
            v-for="t in tabs"
            :key="t.key"
            class="status-tab"
            :class="{ active: activeTab === t.key, disabled: isBusy && t.key !== 'properties' }"
            @click="setTab(t.key)"
          >
            <i :class="t.icon" /> {{ t.label }}
          </button>
        </div>
        <button class="modern-close" title="Закрыть" @click="emit('close')"><i class="pi pi-times" /></button>
      </div>

      <div class="modern-body">
        <div v-if="!series" class="status-loading"><i class="pi pi-spin pi-spinner" style="font-size: 1.6rem" /></div>
        <template v-else>
          <StatusProperties
            v-show="activeTab === 'properties'"
            ref="propsRef"
            :series="series"
            @saving="saving = $event"
          />
          <div v-show="activeTab === 'composition'" class="status-stub">
            Вкладка «Композиция» — следующий шаг переноса статус-окна.
          </div>
          <div v-show="activeTab === 'history'" class="status-stub">
            Вкладка «История» — следующий шаг переноса статус-окна.
          </div>
        </template>
      </div>

      <div class="modern-footer">
        <Button
          v-if="activeTab === 'properties'"
          :label="isBusy ? 'Обработка…' : 'Сохранить'"
          icon="pi pi-check"
          :loading="saving"
          :disabled="isBusy"
          @click="save"
        />
        <Button label="Закрыть" icon="pi pi-times" severity="secondary" @click="emit('close')" />
      </div>
    </div>
  </div>
</template>

<style scoped>
.status-stub { padding: 40px; text-align: center; color: var(--text-muted); }
</style>
