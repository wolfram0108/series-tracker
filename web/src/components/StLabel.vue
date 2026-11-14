<script setup lang="ts">
import { inject, watch } from "vue"
import { GROUP_LABEL } from "../lib/fieldLabel"

// The label of a field as a segment of its group (constructor-item item-label-text-icon): an icon, the words, and a mark by them.
// The first label of a group names the group's controls for the reader of the screen (lib/fieldLabel.ts).
const props = defineProps<{ text: string; icon?: string }>()
const group = inject(GROUP_LABEL, null)
watch(() => props.text, (text, before) => {
  if (group && (!group.value || group.value === before)) group.value = text
}, { immediate: true })
</script>

<template>
  <div class="constructor-item item-label-text-icon">
    <i v-if="icon" :class="icon" aria-hidden="true" />{{ text }}<slot />
  </div>
</template>
