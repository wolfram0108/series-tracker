<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount } from "vue"
import { computeDropStyle, type DropStyle } from "../composables/useDropAnchor"

// Select-айтем внутри группы (constructor-item-select). Безрамочный,
// показывает выбранное + шеврон; список — absolute в относительном
// .constructor-item. Высота списка — по содержимому, потолок — до края
// окна (расчёт при открытии, useDropAnchor); снизу мало места → вверх.
interface Opt { label: string; value: unknown }
const props = withDefaults(defineProps<{
  modelValue?: unknown
  options: Opt[]
  placeholder?: string
}>(), { placeholder: "Выберите" })

const emit = defineEmits<{ "update:modelValue": [unknown] }>()
const open = ref(false)
const root = ref<HTMLElement | null>(null)
const dropStyle = ref<DropStyle | null>(null)
const selText = computed(() => {
  const o = props.options.find((o) => o.value === props.modelValue)
  return o ? o.label : props.placeholder
})

function toggle() {
  if (!open.value && root.value) dropStyle.value = computeDropStyle(root.value)
  open.value = !open.value
}
function pick(o: Opt) {
  emit("update:modelValue", o.value)
  open.value = false
}
function onOutside(e: MouseEvent) {
  if (open.value && root.value && !root.value.contains(e.target as Node)) open.value = false
}
onMounted(() => document.addEventListener("click", onOutside, true))
onBeforeUnmount(() => document.removeEventListener("click", onOutside, true))
</script>

<template>
  <div ref="root" class="constructor-item item-select" :class="{ open }" @click.stop="toggle">
    <span class="selected-value">{{ selText }}</span>
    <i class="pi pi-chevron-down chevron" />
    <div v-if="open" class="options-list" :style="dropStyle ?? undefined">
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
  </div>
</template>
