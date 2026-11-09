<script setup lang="ts">
// The switch that turns an episode or a season on and off: the one of the main screen («Готовность», styles/card.css),
// so that the same control does the same thing everywhere. Its label names what a change of it does.
defineProps<{ on: boolean; label: string; busy?: boolean }>()
const emit = defineEmits<{ (e: "toggle", on: boolean): void }>()
</script>

<template>
  <label class="switch power-switch" data-lever :class="{ 'is-busy': busy }" :title="label" @click.stop>
    <input type="checkbox" :checked="on" :disabled="busy" :aria-label="label" @change="emit('toggle', ($event.target as HTMLInputElement).checked)" />
    <span class="slider" />
  </label>
</template>

<style scoped>
/* The place sets the height (`--switch-size`, the one of the main screen when it does not); the knob and its travel follow. */
.power-switch {
  --size: var(--switch-size, 1.5rem); --knob-gap: calc(var(--size) / 8);
  flex: none; inline-size: calc(var(--size) * 1.8); block-size: var(--size);
}
.power-switch .slider { border-radius: calc(var(--size) / 2); }
.power-switch .slider::before {
  inline-size: calc(var(--size) - 2 * var(--knob-gap)); block-size: calc(var(--size) - 2 * var(--knob-gap));
  inset-inline-start: var(--knob-gap); inset-block-end: var(--knob-gap);
}
.power-switch input:checked + .slider::before { transform: translateX(calc(var(--size) * 0.8)); }
.power-switch.is-busy { opacity: 0.5; }
.power-switch input:focus-visible + .slider { outline: 0.125rem solid var(--color-blue); outline-offset: 0.125rem; }
</style>
