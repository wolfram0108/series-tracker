<script setup lang="ts">
// Рамка constructor-group: монолитный блок, в который через слот
// собираются айтемы (StIcon / StInput / StSelect / item-button-group).
defineProps<{ state?: "valid" | "invalid" | null }>()
</script>

<template>
  <div
    class="input-constructor-group"
    :class="{ 'is-valid': state === 'valid', 'is-invalid': state === 'invalid' }"
  >
    <slot />
  </div>
</template>
