<script setup lang="ts">
import { nextTick, ref } from "vue"
import { useAwaited } from "../../composables/useAwaited"
import { t } from "../../i18n"
import type { ShownVoice } from "../../lib/plan"
import OrderList from "../OrderList.vue"
import VoiceMark from "./output/VoiceMark.vue"

// The tracks of the containers of a season: the voice-overs in their order, the first being the default one, each under
// the name its track bears (the user's name, else the name of the group). The user drags a voice-over to its place and
// renames its track: a press on the name (or Enter on the chip) makes it a field; Enter or leaving the field keeps the
// name, Escape drops it, an empty one goes back to the group's. The core keeps both and sends them back as events; the name typed is shown until it answers.
const props = withDefaults(defineProps<{ voices: ShownVoice[]; busy?: boolean }>(), { busy: false })
const emit = defineEmits<{
  (e: "order", labels: string[]): void
  (e: "title", label: string, title: string): void
}>()

type Titles = Record<string, string>
const { shown: titles, hold } = useAwaited<Titles>(
  () => Object.fromEntries(props.voices.map((voice) => [voice.label, voice.title ?? ""])),
  (each) => JSON.stringify(Object.entries(each).sort()))
const nameOf = (voice: ShownVoice): string => titles.value[voice.label] || voice.label

const editing = ref<string | null>(null)
const draft = ref("")
const field = ref<HTMLInputElement | null>(null)

async function start(voice: ShownVoice): Promise<void> {
  if (props.busy) return
  editing.value = voice.label
  draft.value = nameOf(voice)
  await nextTick()
  field.value?.focus()
  field.value?.select()
}

function keep(voice: ShownVoice): void {
  if (editing.value !== voice.label) return
  editing.value = null
  const typed = draft.value.trim()
  const title = typed === voice.label ? "" : typed
  if (props.busy || title === (titles.value[voice.label] ?? "")) return
  hold({ ...titles.value, [voice.label]: title })
  emit("title", voice.label, title)
}
</script>

<template>
  <section class="voice-order" :aria-label="t('output.voices.title')">
    <span class="voice-order-title">{{ t("output.tracks") }}</span>
    <OrderList
      class="voice-order-list" :items="voices" :item-key="(voice) => voice.label" :busy="busy || editing !== null"
      @order="emit('order', $event.map((voice) => voice.label))"
    >
      <template #item="{ item, index, step }">
        <li
          class="voice-order-item" data-lever :class="{ 'is-busy': busy }" tabindex="0"
          :aria-label="t('output.voices.item', { position: index + 1, label: nameOf(item), episodes: item.episodes })"
          @keydown="step" @keydown.enter.self.prevent="start(item)"
        >
          <VoiceMark :n="index + 1" :online="item.online" />
          <input
            v-if="editing === item.label" ref="field" v-model="draft" class="voice-title-input"
            :size="Math.max(draft.length, item.label.length)" :placeholder="item.label" :aria-label="t('output.voices.titleInput', { label: item.label })"
            @keydown.enter.prevent="keep(item)" @keydown.esc.prevent="editing = null" @blur="keep(item)"
          />
          <span
            v-else class="voice-order-name" @click="start(item)"
            :title="nameOf(item) !== item.label ? t('output.voices.group', { label: item.label }) : t('output.voices.rename')"
          >{{ nameOf(item) }}</span>
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
.voice-order-name { cursor: text; }
.voice-order-item.is-busy .voice-order-name { cursor: not-allowed; }
.voice-title-input {
  font: inherit; padding: 0 0.4rem; border: 1px solid var(--color-blue); border-radius: 10px; background: var(--bg-white);
  color: inherit;
}
</style>
