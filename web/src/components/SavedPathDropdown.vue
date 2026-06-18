<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount } from "vue"
import { api } from "../api/client"

// Выбор сохранённого пути (Настройки → Отладка → «Сохранённые пути»).
// Встраивается в input-constructor-group поля пути компактной кнопкой-
// шевроном без подписи. Сам выбор не хранит: при клике по пути эмитит
// готовое значение (select). Если задан catalogName («Имя (год)
// [tmdbid-XXXX]»), он дописывается в конец через слэш — порт логики
// legacy SavedPathDropdown.js (choose()).
const props = withDefaults(defineProps<{ catalogName?: string }>(), { catalogName: "" })
const emit = defineEmits<{ (e: "select", value: string): void }>()

interface SavedPath { id: number; path: string }
const open = ref(false)
const paths = ref<SavedPath[]>([])
const root = ref<HTMLElement | null>(null)

async function loadPaths() {
  try {
    const { data } = await api.GET("/api/settings/saved_paths")
    const wrapped = data as unknown as { paths?: SavedPath[] } | null
    if (wrapped && Array.isArray(wrapped.paths)) paths.value = wrapped.paths
  } catch {
    // Тихо: список просто пуст, ручной ввод пути не блокируется.
  }
}

async function toggle() {
  if (open.value) {
    open.value = false
    return
  }
  await loadPaths()
  open.value = true
}

function choose(base: string) {
  const name = (props.catalogName || "").trim()
  const full = name ? base.replace(/\/+$/, "") + "/" + name : base
  emit("select", full)
  open.value = false
}

function onOutside(e: MouseEvent) {
  if (open.value && root.value && !root.value.contains(e.target as Node)) open.value = false
}
onMounted(() => document.addEventListener("click", onOutside, true))
onBeforeUnmount(() => document.removeEventListener("click", onOutside, true))
</script>

<template>
  <div
    ref="root"
    class="constructor-item saved-path-trigger"
    :class="{ open }"
    title="Сохранённые пути"
    @click.stop="toggle"
  >
    <i class="pi pi-chevron-down chevron" />
    <div v-if="open" class="options-list">
      <div v-if="!paths.length" class="path-combo-empty">Нет сохранённых путей</div>
      <div v-for="p in paths" :key="p.id" class="option" @click.stop="choose(p.path)">{{ p.path }}</div>
    </div>
  </div>
</template>

