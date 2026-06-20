<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount } from "vue"
import { computeDropStyle, type DropStyle } from "../composables/useDropAnchor"

// Select-айтем внутри группы (constructor-item-select). Безрамочный,
// показывает выбранное + шеврон. Список рендерится через <Teleport to="body">
// с position:fixed (useDropAnchor) — поэтому НЕ обрезается overflow'ом
// предков (модалок/аккордеонов/fieldset'ов). Высота — по содержимому,
// потолок — до края окна; снизу мало места → вверх; репозиция при скролле.
interface Opt { label: string; value: unknown }
const props = withDefaults(defineProps<{
  modelValue?: unknown
  options: Opt[]
  placeholder?: string
}>(), { placeholder: "Выберите" })

const emit = defineEmits<{ "update:modelValue": [unknown] }>()
const open = ref(false)
const root = ref<HTMLElement | null>(null)
const list = ref<HTMLElement | null>(null)
const dropStyle = ref<DropStyle | null>(null)
const selText = computed(() => {
  const o = props.options.find((o) => o.value === props.modelValue)
  return o ? o.label : props.placeholder
})

function reposition() {
  if (root.value) dropStyle.value = computeDropStyle(root.value)
}
function onMove() {
  if (open.value) reposition()
}
function toggle() {
  if (!open.value) reposition() // координаты до открытия — список появляется уже на месте
  open.value = !open.value
}
function pick(o: Opt) {
  emit("update:modelValue", o.value)
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
  <div ref="root" class="constructor-item item-select" :class="{ open }" @click.stop="toggle">
    <span class="selected-value">{{ selText }}</span>
    <i class="pi pi-chevron-down chevron" />
    <Teleport to="body">
      <div v-if="open" ref="list" class="options-list" :style="dropStyle ?? undefined">
        <div
          v-for="o in options"
          :key="String(o.value)"
          class="option"
          :class="{ selected: o.value === modelValue }"
          @click.stop="pick(o)"
        >
          {{ o.label }}
        </div>
      </div>
    </Teleport>
  </div>
</template>
