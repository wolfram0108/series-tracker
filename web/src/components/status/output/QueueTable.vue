<script setup lang="ts">
import Button from "primevue/button"
import { computed, ref, watch } from "vue"
import { t } from "../../../i18n"
import type { QueueName, QueueRow, SourceMark as Mark } from "../../../lib/output"
import type { Held } from "../../../lib/plan"
import { heldFirst, joined, left, rowKey, sizeOf, split, type SameGroups } from "../../../lib/same"
import OrderList from "../../OrderList.vue"
import SameMenu, { type SameItem } from "./SameMenu.vue"
import SourceMark from "./SourceMark.vue"

// A queue of a level: its variants in the order the core tries them. Dragging a row (or moving it with the arrow keys on
// its handle) gives the order of the user, the first row being his choice; the box leaves a variant out of this queue.
// The order and the boxes go to the core, and the queue comes back from it as it then stands.
// The queues of single videos also have the variants the user declared equal (Р-99): the mark of a row picks it, the bar
// over the table makes the picked rows one group, the mark of a group (its number, the colour of the stripe) opens what
// may be done with it. Of the rows of a group the one the file holds is shown first, as the core takes it.
const props = withDefaults(defineProps<{
  rows: QueueRow[]
  queue: QueueName
  marks: Record<number, Mark>
  /** A level that spans episodes: the resolution is the best one there, and the episodes of each variant are shown. */
  level?: boolean
  busy?: boolean
  /** The variant the file holds now: the row that is it says so, whatever its place in the order (the order is a wish, the file is what is). */
  held?: Held | null
  /** The variant the plan takes for the reference of the episode: the row that is it is the reference, whatever its place. */
  taken?: Held | null
}>(), { level: false, busy: false, held: null, taken: null })

const emit = defineEmits<{
  (e: "order", rows: QueueRow[]): void
  (e: "allow", row: QueueRow, allowed: boolean): void
  (e: "same", groups: SameGroups): void
}>()

const keyOf = rowKey
const withSame = computed(() => props.queue !== "voice")
// The rows as they are shown. The list is made anew only when the rows or the variant the file holds change: a window that
// draws again with the same file is given the same list (the list of the order the user dragged waits on it for the core).
const heldKey = computed(() => props.held ? `${props.held.source_id}\u0000${props.held.player}` : "")
const shown = computed(() => {
  if (!withSame.value || !heldKey.value) return props.rows
  const [source, ...player] = heldKey.value.split("\u0000")
  return heldFirst(props.rows, { source_id: Number(source), player: player.join("\u0000") })
})

// The head of the queue: the variant the plan takes (what the user's order only wishes comes second), else the first one taken.
const headOf = (rows: readonly QueueRow[]): QueueRow | undefined => {
  const taken = props.queue === "reference" ? props.taken : null
  return (taken ? rows.find((each) => each.allowed && each.source_id === taken.source_id && each.player === taken.player) : undefined)
    ?? rows.find((each) => each.allowed)
}
const isFirst = (row: QueueRow, rows: readonly QueueRow[]): boolean => headOf(rows) === row

const markOf = (row: QueueRow, rows: readonly QueueRow[]): string => !row.allowed ? t("output.queue.skipped")
  : isFirst(row, rows) ? t(props.queue === "reference" ? "output.queue.reference"
    : props.queue === "markup" ? "output.queue.markup" : "output.queue.taken") : ""
const isHeld = (row: QueueRow): boolean => props.held != null && row.source_id === props.held.source_id && row.player === props.held.player
const height = (row: QueueRow): string => row.height == null ? "—"
  : t(props.level ? "output.heightUpTo" : "output.height", { height: row.height })
const levelOf = (row: QueueRow): string => row.allowed_level === "default" ? "" : t(`output.level.${row.allowed_level}`)

// The groups of equal variants: the rows picked for a new group, and the menu of the mark of a group.
const TONES = 4
const toneOf = (number: number): number => (number - 1) % TONES
const picked = ref(new Set<string>())
watch(() => props.rows, (rows) => {
  const keys = new Set(rows.map(rowKey))
  picked.value = new Set([...picked.value].filter((key) => keys.has(key)))
})
const pick = (row: QueueRow): void => {
  const next = new Set(picked.value)
  if (!next.delete(rowKey(row))) next.add(rowKey(row))
  picked.value = next
}
const join = (): void => {
  emit("same", joined(props.rows, picked.value))
  picked.value = new Set()
}

const menu = ref<{ row: QueueRow; anchor: HTMLElement } | null>(null)
const open = (row: QueueRow, event: MouseEvent): void => {
  const anchor = event.currentTarget as HTMLElement
  menu.value = menu.value?.row === row ? null : { row, anchor }
}
const menuItems = (row: QueueRow): SameItem[] => [
  { key: "pick", label: t("output.queue.samePick") },
  { key: "leave", label: t("output.queue.sameLeave") },
  { key: "split", label: t("output.queue.sameSplit", { count: sizeOf(props.rows, row.same ?? 0) }), danger: true },
]
const onMenu = (key: string): void => {
  const row = menu.value?.row
  menu.value = null
  if (!row) return
  if (key === "pick") pick(row)
  else if (key === "leave") emit("same", left(props.rows, row))
  else if (key === "split" && row.same != null) emit("same", split(props.rows, row.same))
}
</script>

<template>
  <div class="queue-table-wrap">
    <div v-if="withSame && picked.size" class="same-pickbar">
      <span>{{ t("output.queue.samePicked", { count: picked.size }) }}</span>
      <Button :label="t('output.queue.sameJoin')" icon="pi pi-link" size="small" class="same-join" data-lever
              :disabled="busy || picked.size < 2" @click="join" />
      <button type="button" class="queue-back" @click="picked = new Set()">{{ t("output.queue.sameClear") }}</button>
    </div>
    <div class="queue-scroll">
      <table class="queue-table">
        <thead>
          <tr>
            <th class="queue-take">{{ t("output.queue.take") }}</th>
            <th class="queue-handle" />
            <th>{{ t("output.queue.source") }}</th>
            <th>{{ t("output.queue.player") }}</th>
            <th>{{ t("output.queue.height") }}</th>
            <th v-if="queue !== 'voice'">{{ t("output.queue.group") }}</th>
            <th v-if="level">{{ t("output.queue.episodes") }}</th>
            <th v-if="withSame" class="queue-same" :title="t('output.queue.sameHint')" :aria-label="t('output.queue.same')"><i class="pi pi-link" aria-hidden="true" /></th>
            <th />
          </tr>
        </thead>
        <OrderList tag="tbody" :items="shown" :item-key="keyOf" :busy="busy" handle=".order-handle" @order="emit('order', $event)">
          <template #item="{ item: row, items, step }">
            <tr class="variant-row" data-lever :title="row.stale ? t('output.queue.stale', { episodes: row.stale }) : undefined"
                :class="[{ 'is-first': isFirst(row, items), 'is-skipped': !row.allowed, 'is-stale': row.stale !== '' && row.stale === row.episodes },
                         row.same != null ? ['in-same', `same-tone-${toneOf(row.same)}`] : null]">
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
              <td v-if="queue !== 'voice'">{{ row.group }}</td>
              <td v-if="level">{{ row.episodes }}<template v-if="row.stale && row.stale !== row.episodes"> · <span class="stale-episodes">{{ row.stale }}</span></template></td>
              <td v-if="withSame" class="queue-same">
                <button v-if="row.same != null" type="button" class="same-chip" :class="{ on: picked.has(rowKey(row)) }" :disabled="busy" data-lever
                        :title="t('output.queue.sameGroup', { number: row.same })" @click="open(row, $event)">
                  <i class="pi pi-link" aria-hidden="true" />{{ row.same }}
                </button>
                <button v-else type="button" class="same-pick" :class="{ on: picked.has(rowKey(row)) }" :disabled="busy" data-lever
                        :aria-pressed="picked.has(rowKey(row))" :aria-label="t('output.queue.samePick')" @click="pick(row)">
                  <i class="pi pi-link" aria-hidden="true" />
                </button>
              </td>
              <td class="queue-mark-cell"><span class="queue-mark">
                <span v-if="markOf(row, items)" class="pill" :class="row.allowed ? (queue !== 'voice' ? 'pill-primary' : 'pill-success') : 'pill-secondary'">{{ markOf(row, items) }}</span>
                <span v-if="isHeld(row)" class="pill pill-info queue-held">{{ t("output.queue.held") }}</span>
                <span v-if="levelOf(row)" class="queue-level">{{ levelOf(row) }}</span>
              </span></td>
            </tr>
          </template>
        </OrderList>
      </table>
    </div>
    <SameMenu v-if="menu" :anchor="menu.anchor" :items="menuItems(menu.row)" @pick="onMenu" @close="menu = null" />
  </div>
</template>
