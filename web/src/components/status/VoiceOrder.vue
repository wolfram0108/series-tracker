<script setup lang="ts">
import { t } from "../../i18n"
import type { ShownVoice } from "../../lib/plan"
import OrderList from "../OrderList.vue"
import VoiceMark from "./output/VoiceMark.vue"

// The order of the voice-overs of a season: the order of the tracks in the container of every episode, the first being
// the default one. The user drags a voice-over to its place; the core keeps the order and sends it back as an event.
withDefaults(defineProps<{ voices: ShownVoice[]; busy?: boolean }>(), { busy: false })
const emit = defineEmits<{ (e: "order", labels: string[]): void }>()
</script>

<template>
  <section class="voice-order" :aria-label="t('output.voices.title')">
    <span class="voice-order-title">{{ t("output.tracks") }}</span>
    <OrderList
      class="voice-order-list" :items="voices" :item-key="(voice) => voice.label" :busy="busy"
      @order="emit('order', $event.map((voice) => voice.label))"
    >
      <template #item="{ item, index, step }">
        <li
          class="voice-order-item" data-lever :class="{ 'is-busy': busy }" tabindex="0"
          :aria-label="t('output.voices.item', { position: index + 1, label: item.label, episodes: item.episodes })"
          @keydown="step"
        >
          <VoiceMark :n="index + 1" :online="item.online" />
          <span>{{ item.label }}</span>
          <span class="voice-order-episodes">{{ item.episodes }}</span>
        </li>
      </template>
    </OrderList>
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
.voice-order-item:focus-visible { outline: 0.125rem solid var(--color-blue); }
.voice-order-item.is-busy { cursor: not-allowed; opacity: 0.6; }
.voice-order-episodes { color: var(--text-muted); }
</style>
