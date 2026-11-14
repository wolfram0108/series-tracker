<script setup lang="ts">
// A row of a sheet (styles/primitives.css `.sheet`): the caption of what the row holds, its number when it holds a list, and under it
// the body, the whole width. The sheet is every window that holds fields, tables or cards in rows (the settings, the extensions, a series).
defineProps<{ caption: string; count?: number }>()
</script>

<template>
  <section class="sheet-row">
    <h3 class="sheet-caption">{{ caption }}<span v-if="count != null" class="sheet-count">{{ count }}</span></h3>
    <div class="sheet-body"><slot /></div>
  </section>
</template>
