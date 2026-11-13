<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount } from "vue"
import { computeDropStyle, type DropStyle } from "../composables/useDropAnchor"

// A chevron button inside the group of a field: it opens a list of ready values to put into the field. It keeps no
// choice itself: a click on a value emits it. `load` runs before every opening, so the list is as the core has it now.
// The list is teleported into <body> (useDropAnchor): no frame of an ancestor cuts it. One list for every field of this
// kind (the saved paths of a series, the sources and the queries of a series in the test of a profile).
export interface PickItem { value: string; label: string }

const props = withDefaults(defineProps<{
  items: PickItem[]
  title: string
  empty: string
  load?: () => Promise<void>
  minWidth?: number
}>(), { load: undefined, minWidth: 320 })
const emit = defineEmits<{ (e: "select", value: string): void }>()

const open = ref(false)
const root = ref<HTMLElement | null>(null)
const list = ref<HTMLElement | null>(null)
const dropStyle = ref<DropStyle | null>(null)

function reposition(): void {
  if (root.value) dropStyle.value = computeDropStyle(root.value, { align: "right", minWidth: props.minWidth })
}
function onMove(): void {
  if (open.value) reposition()
}

async function toggle(): Promise<void> {
  if (open.value) {
    open.value = false
    return
  }
  await props.load?.()
  reposition()
  open.value = true
}

function choose(value: string): void {
  emit("select", value)
  open.value = false
}

function onOutside(event: MouseEvent): void {
  const target = event.target as Node
  if (open.value && !root.value?.contains(target) && !list.value?.contains(target)) open.value = false
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
  <div ref="root" class="constructor-item pick-trigger" :class="{ open }" :title="title" @click.stop="toggle">
    <i class="pi pi-chevron-down chevron" />
    <Teleport to="body">
      <div v-if="open" ref="list" class="options-list pick-list" :style="dropStyle ?? undefined">
        <div v-if="!items.length" class="pick-empty">{{ empty }}</div>
        <div v-for="item in items" :key="item.value" class="option" @click.stop="choose(item.value)">{{ item.label }}</div>
      </div>
    </Teleport>
  </div>
</template>
