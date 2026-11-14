<script setup lang="ts">
// A tile of a grid of tiles (`.tile-grid`): a name and one line under it.
defineProps<{ title: string; note?: string }>()
</script>

<template>
  <div class="tile">
    <b class="tile-title">{{ title }}</b>
    <span v-if="note" class="tile-note">{{ note }}</span>
  </div>
</template>
