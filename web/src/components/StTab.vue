<script setup lang="ts">
// A tab of a strip of tabs (`.st-tabs`) in the body of a window: the chosen one is bold, and its label keeps the width of
// its bold copy (styles/modal.css .tab-label), so that choosing a tab moves nothing. Marks before the label and counts after
// it come in the slots.
withDefaults(defineProps<{ active: boolean; label: string; role?: string }>(), { role: "tab" })
</script>

<template>
  <button type="button" class="st-tab" :role="role" :class="{ active }" :aria-selected="role === 'tab' ? active : undefined"
          :aria-pressed="role === 'tab' ? undefined : active">
    <slot name="before" />
    <span class="tab-label tab-label-free" :data-text="label"><span class="tl-text">{{ label }}</span></span>
    <slot />
  </button>
</template>
