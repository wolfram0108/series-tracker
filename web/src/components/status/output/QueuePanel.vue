<script setup lang="ts">
import { computed } from "vue"
import type { components } from "../../../api/schema"
import { t } from "../../../i18n"
import type { QueueName, QueueRow, SourceMark as Mark } from "../../../lib/output"
import QueueTable from "./QueueTable.vue"

type QueueView = components["schemas"]["QueueView"] & Partial<Pick<components["schemas"]["GroupQueue"], "group" | "take" | "take_level">>

// One tab of the queues of a level: the hint of the queue, the level its order comes from with the way back to the level
// above, the voice-over taken at all (a group), and the queue itself. The level is an episode, a season or the series.
const props = withDefaults(defineProps<{
  queue: QueueName
  view: QueueView | undefined
  /** The level the panel is for: its own values may go back to the level above. */
  level: "series" | "season" | "episode"
  marks: Record<number, Mark>
  /** The group is the voice of the reference of the episode: its track comes from the video itself. */
  fromReference?: boolean
  busy?: boolean
}>(), { fromReference: false, busy: false })

const emit = defineEmits<{
  (e: "order", rows: QueueRow[]): void
  (e: "allow", row: QueueRow, allowed: boolean): void
  (e: "take", taken: boolean): void
  (e: "reset"): void
}>()

const own = computed(() => props.view?.order_level === props.level)
const hint = computed(() => props.queue === "reference" ? t("output.queue.hintReference")
  : t("output.queue.hintVoice", { group: props.view?.group ?? "" }))
</script>

<template>
  <div class="queue-panel">
    <div class="queue-tools">
      <span>{{ hint }}</span>
      <span class="queue-grow" />
      <label v-if="queue === 'voice' && view" class="queue-take">
        <input type="checkbox" :checked="view.take !== false" :disabled="busy"
               @change="emit('take', ($event.target as HTMLInputElement).checked)" />
        {{ t("output.queue.takeVoice") }}
        <span v-if="view.take_level && view.take_level !== 'default'" class="queue-level">{{ t(`output.level.${view.take_level}`) }}</span>
      </label>
      <span v-if="view" class="queue-level" :class="{ 'is-own': own }">{{ t(`output.level.${view.order_level}`) }}</span>
      <button v-if="own" type="button" class="queue-back" :disabled="busy" @click="emit('reset')">{{ t("output.lever.reset") }}</button>
    </div>
    <p v-if="fromReference" class="queue-note">{{ t("output.queue.fromReference") }}</p>
    <p v-if="view && (view.rows ?? []).length === 0" class="queue-note">{{ t("output.queue.empty") }}</p>
    <QueueTable
      v-else-if="view" :rows="view.rows ?? []" :queue="queue" :marks="marks" :level="level !== 'episode'" :busy="busy"
      @order="emit('order', $event)" @allow="(row, allowed) => emit('allow', row, allowed)"
    />
    <p v-else class="queue-note">{{ t("output.loading") }}</p>
  </div>
</template>

<style scoped>
.queue-panel { display: flex; flex-direction: column; gap: 0.6rem; }
.queue-tools { display: flex; flex-wrap: wrap; align-items: center; gap: 0.5rem 0.75rem; font-size: 0.85rem; }
.queue-grow { flex: 1 1 auto; }
.queue-take { display: inline-flex; align-items: center; gap: 0.4rem; cursor: pointer; }
.queue-level { font-size: 0.75rem; border-radius: 6px; padding: 0.05rem 0.5rem; background: var(--color-gray-200); color: var(--color-gray-600); }
.queue-level.is-own { background: rgba(13, 110, 253, 0.15); color: #0a58ca; font-weight: 600; }
.queue-back { border: none; background: none; color: var(--color-blue); text-decoration: underline; cursor: pointer; padding: 0; font: inherit; }
.queue-back:disabled { opacity: 0.5; cursor: default; }
.queue-note { margin: 0; color: var(--text-muted); font-size: 0.85rem; }
</style>
