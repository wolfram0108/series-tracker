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
  <section class="stack voice-order">
    <span class="voice-order-title">{{ t("output.voices.title") }}</span>
    <ol class="stack voice-order-list">
      <li
        v-for="(voice, index) in voices" :key="voice.label"
        class="cluster voice-order-item" :class="{ 'is-over': over === index, 'is-busy': busy }"
        :draggable="!busy"
        :aria-label="t('output.voices.item', { position: index + 1, label: voice.label, episodes: voice.episodes })"
        @dragstart="dragged = index" @dragover.prevent="over = index" @dragleave="over = null" @drop.prevent="drop(index)"
        @dragend="dragged = over = null"
      >
        <i class="pi pi-bars voice-order-handle" aria-hidden="true" />
        <span class="voice-order-position">{{ index + 1 }}</span>
        <span>{{ t("output.voices.label", { label: voice.label, episodes: voice.episodes }) }}</span>
      </li>
    </ol>
  </section>
</template>

<style scoped>
.voice-order-title { font-weight: 600; }
.voice-order-list { margin: 0; padding: 0; list-style: none; }
.voice-order-item {
  align-items: center; padding: 0.4em 0.6em; border: 1px solid var(--border-color, currentColor);
  border-radius: 0.4em; cursor: grab; background: var(--surface-card, transparent);
}
.voice-order-item.is-over { outline: 2px dashed var(--primary-color, currentColor); }
.voice-order-item.is-busy { cursor: not-allowed; opacity: 0.6; }
.voice-order-handle, .voice-order-position { color: var(--text-muted); }
</style>
