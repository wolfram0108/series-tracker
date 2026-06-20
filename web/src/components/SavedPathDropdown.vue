<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount } from "vue"
import { api } from "../api/client"
import { computeDropStyle, type DropStyle } from "../composables/useDropAnchor"

// Выбор сохранённого пути (Настройки → Отладка → «Сохранённые пути»).
// Встраивается в input-constructor-group поля пути компактной кнопкой-
// шевроном без подписи. Сам выбор не хранит: при клике по пути эмитит
// готовое значение (select). Если задан catalogName («Имя (год)
// [tmdbid-XXXX]»), он дописывается в конец через слэш — порт логики
// legacy SavedPathDropdown.js (choose()).
// Список — через <Teleport to="body"> (position:fixed, useDropAnchor):
// не обрезается overflow'ом предков; прижат к правому краю триггера.
const props = withDefaults(defineProps<{ catalogName?: string }>(), { catalogName: "" })
const emit = defineEmits<{ (e: "select", value: string): void }>()

interface SavedPath { id: number; path: string }
const open = ref(false)
const paths = ref<SavedPath[]>([])
const root = ref<HTMLElement | null>(null)
const list = ref<HTMLElement | null>(null)
const dropStyle = ref<DropStyle | null>(null)

function reposition() {
  if (root.value) dropStyle.value = computeDropStyle(root.value, { align: "right", minWidth: 320 })
}
function onMove() {
  if (open.value) reposition()
}

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
  reposition()
  open.value = true
}

function choose(base: string) {
  const name = (props.catalogName || "").trim()
  const full = name ? base.replace(/\/+$/, "") + "/" + name : base
  emit("select", full)
  open.value = false
}

function onOutside(e: MouseEvent) {
  const t = e.target as Node
  if (open.value && !root.value?.contains(t) && !list.value?.contains(t)) open.value = false
}
onMounted(() => {
  document.addEventListener("click", onOutside, true)
  window.addEventListener("scroll", onMove, true)
  window.addEventListener("resize", onMove)
})
onBeforeUnmount(() => {
  document.removeEventListener("click", onOutside, true)
  window.removeEventListener("scroll", onMove, true)
  window.removeEventListener("resize", onMove)
})
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
    <Teleport to="body">
      <div v-if="open" ref="list" class="options-list" :style="dropStyle ?? undefined">
        <div v-if="!paths.length" class="path-combo-empty">Нет сохранённых путей</div>
        <div v-for="p in paths" :key="p.id" class="option" @click.stop="choose(p.path)">{{ p.path }}</div>
      </div>
    </Teleport>
  </div>
</template>
