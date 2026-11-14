<script setup lang="ts">
// Рамка constructor-group: монолитный блок, в который через слот
// собираются айтемы (StLabel / StInput / StSelect / item-button-group). Поле листа собирает её само — SheetField.
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
