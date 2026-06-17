<script setup lang="ts">
// Иконочная кнопка внутри группы (btn-icon с градиентом по варианту).
defineProps<{
  icon: string
  variant?: "add" | "edit" | "delete" | "confirm" | "cancel" | "search"
  disabled?: boolean
  title?: string
}>()
</script>

<template>
  <button
    type="button"
    class="btn-icon"
    :class="variant ? `btn-${variant}` : ''"
    :disabled="disabled"
    :title="title"
  >
    <i :class="icon" />
  </button>
</template>
