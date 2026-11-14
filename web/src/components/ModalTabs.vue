<script setup lang="ts" generic="V extends string">
// The tabs of the head of a window (the island of `.st-tabs` that ModalShell fits to its room): one button each, the chosen one
// marked. The settings and the extensions take their tabs from here; the body of the chosen tab is the window's.
defineProps<{ tabs: readonly { label: string; value: V; icon: string }[]; modelValue: V }>()
const emit = defineEmits<{ "update:modelValue": [V] }>()
</script>

<template>
  <div class="header-tabs st-tabs">
    <button
      v-for="tb in tabs"
      :key="tb.value"
      type="button"
      class="st-tab"
      :class="{ active: modelValue === tb.value }"
      :title="tb.label"
      @click="emit('update:modelValue', tb.value)"
    >
      <i class="pi tab-icon" :class="tb.icon"></i>
      <span class="tab-label" :data-text="tb.label"><span class="tl-text">{{ tb.label }}</span></span>
    </button>
  </div>
</template>
