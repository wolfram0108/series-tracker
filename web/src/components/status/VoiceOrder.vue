<script setup lang="ts">
import { ref } from "vue"
import { t } from "../../i18n"
import type { components } from "../../api/schema"

type VoiceSpan = components["schemas"]["VoiceSpan"]

// The order of the voice-overs of a season: the order of the tracks in the container of every episode, the first being
// the default one. The user drags a voice-over to its place; the core keeps the order and sends it back as an event.
const props = withDefaults(defineProps<{ voices: VoiceSpan[]; busy?: boolean }>(), { busy: false })
const emit = defineEmits<{ (e: "order", labels: string[]): void }>()

const dragged = ref<number | null>(null)
const over = ref<number | null>(null)

function drop(target: number): void {
  const from = dragged.value
  dragged.value = over.value = null
  if (from === null || from === target || props.busy) return
  const labels = props.voices.map((voice) => voice.label)
  const [moved] = labels.splice(from, 1)
  labels.splice(target, 0, moved)
  emit("order", labels)
}
</script>

<template>
  <section class="voice-order" :aria-label="t('output.voices.title')">
    <span class="voice-order-title">{{ t("output.tracks") }}</span>
    <ol class="voice-order-list">
      <li
        v-for="(voice, index) in voices" :key="voice.label"
        class="voice-order-item" data-lever :class="{ 'is-over': over === index, 'is-busy': busy }"
        :draggable="!busy"
        :aria-label="t('output.voices.item', { position: index + 1, label: voice.label, episodes: voice.episodes })"
        @dragstart="dragged = index" @dragover.prevent="over = index" @dragleave="over = null" @drop.prevent="drop(index)"
        @dragend="dragged = over = null"
      >
        <span class="figure-mark">{{ index + 1 }}</span>
        <span>{{ voice.label }}</span>
        <span class="voice-order-episodes">{{ voice.episodes }}</span>
      </li>
    </ol>
  </section>
</template>

<style scoped>
.voice-order { display: flex; flex-wrap: wrap; align-items: center; gap: 0.4rem 0.5rem; }
.voice-order-title { color: var(--text-muted); font-size: 0.85rem; }
.voice-order-list { display: flex; flex-wrap: wrap; gap: 0.4rem; margin: 0; padding: 0; list-style: none; }
.voice-order-item {
  display: inline-flex; align-items: center; gap: 0.5rem; padding: 0.2rem 0.75rem 0.2rem 0.25rem; cursor: grab;
  background: var(--bg-light); border: 1px solid var(--border-color); border-radius: 20px; font-size: 0.85rem; font-weight: 500;
  color: var(--color-gray-600);
}
.voice-order-item.is-over { outline: 0.125rem dashed var(--color-blue); }
.voice-order-item.is-busy { cursor: not-allowed; opacity: 0.6; }
.voice-order-episodes { color: var(--text-muted); }
</style>
