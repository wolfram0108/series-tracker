<script setup lang="ts">
import StGroup from "./StGroup.vue"
import StIcon from "./StIcon.vue"
import StInput from "./StInput.vue"

// Одиночное поле = группа + (иконка) + floating-поле. Для нагруженных
// строк собирай StGroup с несколькими StIcon/StInput/StSelect напрямую.
defineProps<{
  modelValue?: string
  label: string
  icon?: string
  type?: "text" | "password"
  state?: "valid" | "invalid" | null
}>()
const emit = defineEmits<{ "update:modelValue": [string] }>()
</script>

<template>
  <StGroup :state="state">
    <StIcon v-if="icon" :icon="icon" />
    <StInput
      :model-value="modelValue"
      :label="label"
      :type="type"
      @update:model-value="emit('update:modelValue', $event)"
    />
  </StGroup>
</template>
