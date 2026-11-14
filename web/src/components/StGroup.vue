<script setup lang="ts">
import { inject, provide, ref } from "vue"
import { FIELD_LABEL, GROUP_LABEL } from "../lib/fieldLabel"

// Рамка constructor-group: монолитный блок, в который через слот
// собираются айтемы (StLabel / StInput / StSelect / item-button-group). Поле листа собирает её само — SheetField.
// The controls inside name themselves by the label segment of the group (lib/fieldLabel.ts), where the group has one.
defineProps<{ state?: "valid" | "invalid" | null }>()
const outer = inject(FIELD_LABEL, null)
const label = ref("")
provide(GROUP_LABEL, label)
provide(FIELD_LABEL, () => label.value || outer?.() || "")
</script>

<template>
  <div
    class="input-constructor-group"
    :class="{ 'is-valid': state === 'valid', 'is-invalid': state === 'invalid' }"
  >
    <slot />
  </div>
</template>
