<script setup lang="ts">
// The label of a field as a segment of its group (constructor-item item-label-text-icon): an icon, the words, and a mark by them.
defineProps<{ text: string; icon?: string }>()
</script>

<template>
  <div class="constructor-item item-label-text-icon">
    <i v-if="icon" :class="icon" />{{ text }}<slot />
  </div>
</template>
