<script setup lang="ts">
// The switch that turns an episode or a season on and off: the one of the main screen («Готовность», styles/card.css),
// so that the same control does the same thing everywhere. Its label names what a change of it does.
defineProps<{ on: boolean; label: string; busy?: boolean }>()
const emit = defineEmits<{ (e: "toggle", on: boolean): void }>()
</script>

<template>
  <label class="switch power-switch" :class="{ 'is-busy': busy }" :title="label" @click.stop>
    <input type="checkbox" :checked="on" :disabled="busy" :aria-label="label" @change="emit('toggle', ($event.target as HTMLInputElement).checked)" />
    <span class="slider" />
  </label>
</template>

<style scoped>
.power-switch { flex: none; }
.power-switch.is-busy { opacity: 0.5; }
.power-switch input:focus-visible + .slider { outline: 0.125rem solid var(--color-blue); outline-offset: 0.125rem; }
</style>
