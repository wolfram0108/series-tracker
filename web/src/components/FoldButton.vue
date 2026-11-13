<script setup lang="ts">
import Button from "primevue/button"

// The folding button: a word with its icon, and its icon alone where its room is short of the width its tier names
// (styles/primitives.css, `.fold-room` and `.fold-sm|md|lg`). `hint` says what it does — its title and its name for a reader of
// the screen — and is what is left of it when the word goes. The look is a button of the window (PrimeVue, small); `severity`
// is its colour: the one of what it does, never the one of another action.
withDefaults(defineProps<{
  label: string
  icon: string
  hint?: string
  severity?: "secondary" | "info" | "success" | "warn" | "danger" | "contrast" | "help"
  fold?: "sm" | "md" | "lg" | "xl"
}>(), { hint: undefined, severity: undefined, fold: "md" })
</script>

<template>
  <Button
    class="fold-btn" :class="`fold-${fold}`" :label="label" :icon="icon" :severity="severity" size="small"
    :title="hint ?? label" :aria-label="hint ?? label"
  />
</template>
