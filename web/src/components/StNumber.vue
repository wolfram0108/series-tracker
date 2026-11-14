<script setup lang="ts">
import { inject } from "vue"
import { FIELD_LABEL } from "../lib/fieldLabel"

// The number of a field of a sheet (SheetField with `control`): a short field the value of which is a whole or a decimal number, kept as
// the text typed until its owner reads it. It is the bare input of a group (`item-input`) as every field of a sheet is — the frame is
// the group's — and it names itself by the label of its field. `change` (a field left, Enter) is the native one.
defineProps<{ modelValue: string; decimal?: boolean }>()
const emit = defineEmits<{ "update:modelValue": [string] }>()
const label = inject(FIELD_LABEL, null)
</script>

<template>
  <input
    class="item-input st-number" :value="modelValue" :inputmode="decimal ? 'decimal' : 'numeric'" :aria-label="label?.()"
    @input="emit('update:modelValue', ($event.target as HTMLInputElement).value)"
  />
</template>
