<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from "vue"
import { computeDropStyle, type DropStyle } from "../../../composables/useDropAnchor"
import { useLayer } from "../../../composables/useLayers"

// The menu of a mark of a group of equal variants: what the mark may be told to do. It stands under the mark, in <body>
// (useDropAnchor), so that no frame of the table cuts it; a click outside, Escape or a scroll closes it.
export interface SameItem { key: string; label: string; danger?: boolean }

const props = defineProps<{ anchor: HTMLElement | null; items: SameItem[] }>()
const emit = defineEmits<{ (e: "pick", key: string): void; (e: "close"): void }>()

const list = ref<HTMLElement | null>(null)
const style = ref<DropStyle | null>(null)

const place = (): void => {
  if (props.anchor) style.value = computeDropStyle(props.anchor, { align: "right", minWidth: 240, min: 100 })
}
const onOutside = (event: MouseEvent): void => {
  const target = event.target as Node
  if (!list.value?.contains(target) && !props.anchor?.contains(target)) emit("close")
}
// Escape closes the menu alone (a layer): the window under it stays.
useLayer(() => emit("close")).enter()

watch(() => props.anchor, place, { immediate: true })
onMounted(() => {
  document.addEventListener("click", onOutside, true)
  window.addEventListener("scroll", place, true)
  window.addEventListener("resize", place)
})
onBeforeUnmount(() => {
  document.removeEventListener("click", onOutside, true)
  window.removeEventListener("scroll", place, true)
  window.removeEventListener("resize", place)
})
</script>

<template>
  <Teleport to="body">
    <div ref="list" class="options-list same-menu" role="menu" :style="style ?? undefined">
      <button v-for="item in items" :key="item.key" type="button" role="menuitem" class="option same-option"
              :class="{ 'is-danger': item.danger }" @click.stop="emit('pick', item.key)">{{ item.label }}</button>
    </div>
  </Teleport>
</template>
