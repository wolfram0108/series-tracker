<script setup lang="ts">
import { inject } from "vue"
import ToggleSwitch from "primevue/toggleswitch"
import { FIELD_LABEL } from "../lib/fieldLabel"

// The switch of a field of a sheet (SheetField with `control`): it names itself by the label of its field.
defineProps<{ modelValue: boolean }>()
const emit = defineEmits<{ "update:modelValue": [boolean] }>()
const label = inject(FIELD_LABEL, null)
</script>

<template>
  <ToggleSwitch :model-value="modelValue" :aria-label="label?.()" @update:model-value="emit('update:modelValue', $event)" />
</template>
