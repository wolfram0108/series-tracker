<script setup lang="ts">
import { t } from "../../../i18n"
import type { QueueName, QueueRow, SourceMark as Mark } from "../../../lib/output"
import OrderList from "../../OrderList.vue"
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
// The first variant taken, in the order the list shows (the one dragged, while the core has not answered yet).
const isFirst = (row: QueueRow, rows: readonly QueueRow[]): boolean => rows.find((each) => each.allowed) === row

const markOf = (row: QueueRow, rows: readonly QueueRow[]): string => !row.allowed ? t("output.queue.skipped")
  : isFirst(row, rows) ? t(props.queue === "reference" ? "output.queue.reference" : "output.queue.taken") : ""
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
    <OrderList tag="tbody" :items="rows" :item-key="keyOf" :busy="busy" handle=".order-handle" @order="emit('order', $event)">
      <template #item="{ item: row, items, step }">
        <tr class="variant-row" data-lever :class="{ 'is-first': isFirst(row, items), 'is-skipped': !row.allowed }">
          <td class="queue-take">
            <input type="checkbox" :checked="row.allowed" :disabled="busy" :aria-label="t('output.queue.take')"
                   @change="emit('allow', row, ($event.target as HTMLInputElement).checked)" />
          </td>
          <td class="queue-handle">
            <button type="button" class="order-handle" :disabled="busy" :aria-label="t('output.queue.drag')" @keydown="step">
              <i class="pi pi-bars" aria-hidden="true" />
            </button>
          </td>
          <td><span class="queue-source"><SourceMark :mark="marks[row.source_id]" /><span class="queue-place">{{ marks[row.source_id]?.place }}</span></span></td>
          <td class="queue-player">{{ row.player_label }}</td>
          <td>{{ height(row) }}</td>
          <td v-if="queue === 'reference'">{{ row.group }}</td>
          <td v-if="level">{{ row.episodes }}</td>
          <td class="queue-mark-cell"><span class="queue-mark">
            <span v-if="markOf(row, items)" class="pill" :class="row.allowed ? (queue === 'reference' ? 'pill-primary' : 'pill-success') : 'pill-secondary'">{{ markOf(row, items) }}</span>
            <span v-if="levelOf(row)" class="queue-level">{{ levelOf(row) }}</span>
          </span></td>
        </tr>
      </template>
    </OrderList>
  </table>
</template>
