<script setup lang="ts">
import { inject } from "vue"
import InputText from "primevue/inputtext"
import { FIELD_LABEL } from "../lib/fieldLabel"

// The number of a field of a sheet (SheetField with `control`): a short field the value of which is a whole or a decimal number, kept as
// the text typed until its owner reads it; it names itself by the label of its field. `change` (a field left, Enter) is the native one.
defineProps<{ modelValue: string; decimal?: boolean }>()
const emit = defineEmits<{ "update:modelValue": [string] }>()
const label = inject(FIELD_LABEL, null)
</script>

<template>
  <InputText
    :model-value="modelValue" :inputmode="decimal ? 'decimal' : 'numeric'" class="st-number" :aria-label="label?.()"
    @update:model-value="emit('update:modelValue', String($event ?? ''))"
  />
</template>
