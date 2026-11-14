<script setup lang="ts">
// The segment of a group that shows a value that is not typed here: a folder, an address another window sets.
defineProps<{ value: string; mono?: boolean }>()
</script>

<template>
  <div class="constructor-item item-value" :class="{ 'font-mono': mono }">{{ value }}</div>
</template>
