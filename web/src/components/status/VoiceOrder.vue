<script setup lang="ts">
import { computed, nextTick, ref } from "vue"
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
// The voice-over of the markup reference stands last in the files whatever the order: it is shown after the list, fixed,
// and the order the user drags keeps it in its place at the end.
const movable = computed(() => props.voices.filter((voice) => !voice.last))
const fixed = computed(() => props.voices.filter((voice) => voice.last))

const editing = ref<string | null>(null)
const draft = ref("")
// One field at a time; the fixed chips stand in a v-for of this template, where a ref is a list.
const field = ref<HTMLInputElement | HTMLInputElement[] | null>(null)

async function start(voice: ShownVoice): Promise<void> {
  if (props.busy) return
  editing.value = voice.label
  draft.value = nameOf(voice)
  await nextTick()
  const input = Array.isArray(field.value) ? field.value[0] : field.value
  input?.focus()
  input?.select()
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
      class="voice-order-list" :items="movable" :item-key="(voice) => voice.label" :busy="busy || editing !== null"
      @order="emit('order', [...$event, ...fixed].map((voice) => voice.label))"
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
    <ol v-if="fixed.length" class="voice-order-list">
      <li
        v-for="(item, index) in fixed" :key="item.label" class="voice-order-item is-fixed" :class="{ 'is-busy': busy }"
        tabindex="0" :title="t('output.voices.markup')"
        :aria-label="t('output.voices.item', { position: movable.length + index + 1, label: nameOf(item), episodes: item.episodes })"
        @keydown.enter.self.prevent="start(item)"
      >
        <VoiceMark :n="movable.length + index + 1" :online="item.online" />
        <input
          v-if="editing === item.label" ref="field" v-model="draft" class="voice-title-input"
          :size="Math.max(draft.length, item.label.length)" :placeholder="item.label" :aria-label="t('output.voices.titleInput', { label: item.label })"
          @keydown.enter.prevent="keep(item)" @keydown.esc.prevent="editing = null" @blur="keep(item)"
        />
        <span v-else class="voice-order-name" @click="start(item)">{{ nameOf(item) }}</span>
        <i class="pi pi-bookmark-fill voice-order-markup" aria-hidden="true" />
        <span class="voice-order-episodes">{{ item.episodes }}</span>
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
.voice-order-item:focus-visible { outline: 0.125rem solid var(--color-blue); }
.voice-order-item.is-busy { cursor: not-allowed; opacity: 0.6; }
.voice-order-episodes { color: var(--text-muted); }
.voice-order-item.is-fixed { cursor: default; }
.voice-order-markup { color: var(--text-muted); font-size: 0.8em; }
.voice-order-name { cursor: text; }
.voice-order-item.is-busy .voice-order-name { cursor: not-allowed; }
.voice-title-input {
  font: inherit; padding: 0 0.4rem; border: 1px solid var(--color-blue); border-radius: 10px; background: var(--bg-white);
  color: inherit;
}
</style>
