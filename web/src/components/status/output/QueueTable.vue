<script setup lang="ts">
import { computed, ref } from "vue"
import { t } from "../../../i18n"
import type { QueueName, QueueRow, SourceMark as Mark } from "../../../lib/output"
import SourceMark from "./SourceMark.vue"

// A queue of a level: its variants in the order the core tries them. Dragging a row (or moving it with the arrow keys on
// its handle) gives the order of the user, the first row being his choice; the box leaves a variant out of this queue.
// The order and the boxes go to the core, and the queue comes back from it as it then stands.
const props = withDefaults(defineProps<{
  rows: QueueRow[]
  queue: QueueName
  marks: Record<number, Mark>
  /** A level that spans episodes: the resolution is the best one there, and the episodes of each variant are shown. */
  level?: boolean
  busy?: boolean
}>(), { level: false, busy: false })

const emit = defineEmits<{
  (e: "order", rows: QueueRow[]): void
  (e: "allow", row: QueueRow, allowed: boolean): void
}>()

const keyOf = (row: QueueRow): string => JSON.stringify([row.group, row.source_id, row.player])
const first = computed(() => props.rows.find((row) => row.allowed) ?? null)
const dragged = ref<number | null>(null)
const over = ref<number | null>(null)

function move(from: number, to: number): void {
  if (props.busy || from === to || to < 0 || to >= props.rows.length) return
  const rows = [...props.rows]
  rows.splice(to, 0, rows.splice(from, 1)[0])
  emit("order", rows)
}

function drop(index: number): void {
  const from = dragged.value
  dragged.value = over.value = null
  if (from !== null) move(from, index)
}

function onKey(event: KeyboardEvent, index: number): void {
  if (event.key === "ArrowUp" || event.key === "ArrowDown") {
    event.preventDefault()
    move(index, index + (event.key === "ArrowUp" ? -1 : 1))
  }
}

const markOf = (row: QueueRow): string => !row.allowed ? t("output.queue.skipped")
  : first.value && keyOf(first.value) === keyOf(row) ? t(props.queue === "reference" ? "output.queue.reference" : "output.queue.taken") : ""
const height = (row: QueueRow): string => row.height == null ? "—"
  : t(props.level ? "output.heightUpTo" : "output.height", { height: row.height })
const levelOf = (row: QueueRow): string => row.allowed_level === "default" ? "" : t(`output.level.${row.allowed_level}`)
</script>

<template>
  <table class="queue-table">
    <thead>
      <tr>
        <th class="queue-take">{{ t("output.queue.take") }}</th>
        <th class="queue-handle" />
        <th>{{ t("output.queue.source") }}</th>
        <th>{{ t("output.queue.player") }}</th>
        <th>{{ t("output.queue.height") }}</th>
        <th v-if="queue === 'reference'">{{ t("output.queue.group") }}</th>
        <th v-if="level">{{ t("output.queue.episodes") }}</th>
        <th />
      </tr>
    </thead>
    <tbody>
      <tr
        v-for="(row, index) in rows" :key="keyOf(row)" draggable="true" class="variant-row" data-lever
        :class="{ 'is-first': first && keyOf(first) === keyOf(row), 'is-skipped': !row.allowed, 'is-over': over === index }"
        @dragstart="dragged = index" @dragover.prevent="over = index" @dragleave="over = null" @drop.prevent="drop(index)"
        @dragend="dragged = over = null"
      >
        <td class="queue-take">
          <input type="checkbox" :checked="row.allowed" :disabled="busy" :aria-label="t('output.queue.take')"
                 @change="emit('allow', row, ($event.target as HTMLInputElement).checked)" />
        </td>
        <td class="queue-handle">
          <button type="button" class="queue-drag" :disabled="busy" :aria-label="t('output.queue.drag')" @keydown="onKey($event, index)">
            <i class="pi pi-bars" aria-hidden="true" />
          </button>
        </td>
        <td><span class="queue-source"><SourceMark :mark="marks[row.source_id]" /><span class="queue-place">{{ marks[row.source_id]?.place }}</span></span></td>
        <td class="queue-player">{{ row.player_label }}</td>
        <td>{{ height(row) }}</td>
        <td v-if="queue === 'reference'">{{ row.group }}</td>
        <td v-if="level">{{ row.episodes }}</td>
        <td class="queue-mark-cell"><span class="queue-mark">
          <span v-if="markOf(row)" class="pill" :class="row.allowed ? (queue === 'reference' ? 'pill-primary' : 'pill-success') : 'pill-secondary'">{{ markOf(row) }}</span>
          <span v-if="levelOf(row)" class="queue-level">{{ levelOf(row) }}</span>
        </span></td>
      </tr>
    </tbody>
  </table>
</template>
