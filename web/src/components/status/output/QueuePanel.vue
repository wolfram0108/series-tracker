<script setup lang="ts">
import { computed } from "vue"
import type { components } from "../../../api/schema"
import { t } from "../../../i18n"
import type { QueueName, QueueRow, SourceMark as Mark } from "../../../lib/output"
import type { Held } from "../../../lib/plan"
import QueueTable from "./QueueTable.vue"

type QueueView = components["schemas"]["QueueView"] & Partial<Pick<components["schemas"]["GroupQueue"], "group" | "take" | "take_level">>

// One tab of the queues of a level: the hint of the queue, the level its order comes from with the way back to the level
// above, the voice-over taken at all (a group), and the queue itself. The level is an episode, a season or the series.
const props = withDefaults(defineProps<{
  queue: QueueName
  view: QueueView | undefined
  /** The level the panel is for: its own values may go back to the level above. */
  level: "series" | "season" | "compilation" | "episode"
  marks: Record<number, Mark>
  /** The group is the voice of the reference of the episode: its track comes from the video itself. */
  fromReference?: boolean
  busy?: boolean
  /** The words over the queue, when they are not those of its kind (REF 2 of a compilation, stage 12). */
  hint?: string
  /** The queue of the markup reference follows the reference: no level has an order of its own (stage 14). */
  follows?: boolean
  /** The variant the file of the episode holds now (its source and player): the fact the order of the queue is read against. */
  held?: Held | null
}>(), { fromReference: false, busy: false, hint: undefined, follows: false, held: null })

const emit = defineEmits<{
  (e: "order", rows: QueueRow[]): void
  (e: "allow", row: QueueRow, allowed: boolean): void
  (e: "take", taken: boolean): void
  (e: "reset"): void
}>()

const own = computed(() => props.view?.order_level === props.level)
const hint = computed(() => props.hint ?? (props.queue === "reference" ? t("output.queue.hintReference")
  : props.queue === "markup" ? t(props.follows ? "output.queue.hintMarkupFollows" : "output.queue.hintMarkup")
    : t("output.queue.hintVoice", { group: props.view?.group ?? "" })))
</script>

<template>
  <div class="queue-panel">
    <div class="queue-tools">
      <span>{{ hint }}</span>
      <span class="queue-grow" />
      <label v-if="queue === 'voice' && view" class="queue-take" data-lever>
        <input type="checkbox" :checked="view.take !== false" :disabled="busy"
               @change="emit('take', ($event.target as HTMLInputElement).checked)" />
        {{ t("output.queue.takeVoice") }}
        <span v-if="view.take_level && view.take_level !== 'default'" class="queue-level">{{ t(`output.level.${view.take_level}`) }}</span>
      </label>
      <span v-if="view && follows" class="queue-level queue-follows">{{ t("output.queue.followsReference") }}</span>
      <span v-else-if="view" class="queue-level" :class="{ 'is-own': own }">{{ t(`output.level.${view.order_level}`) }}</span>
      <button v-if="own" type="button" class="queue-back" data-lever :disabled="busy" @click="emit('reset')">{{ t("output.lever.reset") }}</button>
    </div>
    <p v-if="fromReference" class="queue-note">{{ t("output.queue.fromReference") }}</p>
    <p v-if="view && (view.rows ?? []).length === 0" class="queue-note">{{ t("output.queue.empty") }}</p>
    <QueueTable
      v-else-if="view" :rows="view.rows ?? []" :queue="queue" :marks="marks" :level="level !== 'episode'" :held="held" :busy="busy"
      @order="emit('order', $event)" @allow="(row, allowed) => emit('allow', row, allowed)"
    />
    <p v-else class="queue-note">{{ t("common.fetching") }}</p>
  </div>
</template>
