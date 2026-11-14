<script setup lang="ts">
import { provide } from "vue"
import { FIELD_LABEL } from "../lib/fieldLabel"
import StControl from "./StControl.vue"
import StGroup from "./StGroup.vue"
import StLabel from "./StLabel.vue"

// A field of a sheet: its label as the first segment of a group (an icon and the words, a mark by them in the slot `mark`), then what
// holds the value — an input, a select, or with `control` a control in its own segment (a switch, a number, a pair of buttons).
// The controls inside name themselves by the label (lib/fieldLabel.ts), so that it is written once. `wide` takes the whole row of
// `.sheet-pair`; a field with a hint (`title`) says it on hover.
const props = defineProps<{
  label: string
  icon?: string
  title?: string
  state?: "valid" | "invalid" | null
  wide?: boolean
  control?: boolean
}>()
provide(FIELD_LABEL, () => props.label)
</script>

<template>
  <StGroup :state="state" :title="title" :class="{ 'is-wide': wide }">
    <StLabel :icon="icon" :text="label"><slot name="mark" /></StLabel>
    <StControl v-if="control"><slot /></StControl>
    <slot v-else />
  </StGroup>
</template>
