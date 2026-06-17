<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount } from "vue"

// Select-айтем внутри группы (constructor-item-select). Безрамочный,
// показывает выбранное + шеврон; список — absolute в относительном
// .constructor-item (чисто, без fixed-координат).
interface Opt { label: string; value: unknown }
const props = withDefaults(defineProps<{
  modelValue?: unknown
  options: Opt[]
  placeholder?: string
}>(), { placeholder: "Выберите" })

const emit = defineEmits<{ "update:modelValue": [unknown] }>()
const open = ref(false)
const root = ref<HTMLElement | null>(null)
const selText = computed(() => {
  const o = props.options.find((o) => o.value === props.modelValue)
  return o ? o.label : props.placeholder
})

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
  <div ref="root" class="constructor-item item-select" :class="{ open }" @click.stop="open = !open">
    <span class="selected-value">{{ selText }}</span>
    <i class="pi pi-chevron-down chevron" />
    <div v-if="open" class="options-list">
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
