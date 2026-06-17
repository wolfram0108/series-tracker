<script setup lang="ts">
// Иконка-сегмент (constructor-item item-label-icon).
defineProps<{ icon: string }>()
</script>

<template>
  <div class="constructor-item item-label-icon"><i :class="icon" /></div>
</template>
