<script setup lang="ts">
import Button from "primevue/button"
import { computed, ref, watch } from "vue"
import { t, type MessageKey } from "../../../i18n"
import type { QueueName, QueueRow, SourceMark as Mark, VariantFlag } from "../../../lib/output"
import { isVariantOf, type FailedAttempt, type Held } from "../../../lib/plan"
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
  /** The attempts of the plan that failed for the episode: the row of the variant an attempt was of says so. */
  failed?: FailedAttempt[]
}>(), { level: false, busy: false, held: null, taken: null, failed: () => [] })

const emit = defineEmits<{
  (e: "order", rows: QueueRow[]): void
  (e: "allow", row: QueueRow, allowed: boolean, flag: VariantFlag): void
  (e: "same", groups: SameGroups): void
}>()

const keyOf = rowKey
// The subtitles are tracks, not a queue of choices (DESIGN.md 7.1.1): every row taken is a track, no row stands first, and
// their columns are their name, language and type, not a player and a height.
const subtitles = computed(() => props.queue === "subtitles")
const withSame = computed(() => props.queue !== "voice" && !subtitles.value)
// The rows as they are shown. The list is made anew only when the rows or the variant the file holds change: a window that
// draws again with the same file is given the same list (the list of the order the user dragged waits on it for the core).
const heldKey = computed(() => props.held ? JSON.stringify([props.held.source_id, props.held.player, props.held.group ?? null]) : "")
const shown = computed(() => {
  if (!withSame.value || !heldKey.value) return props.rows
  const [source_id, player, group] = JSON.parse(heldKey.value) as [number, string, string | null]
  return heldFirst(props.rows, { source_id, player, ...(group === null ? {} : { group }) })
})

// The head of the queue: the variant the plan takes (what the user's order only wishes comes second), else the first one taken.
const headOf = (rows: readonly QueueRow[]): QueueRow | undefined => {
  const taken = props.taken
  return rows.find((each) => each.allowed && isVariantOf(taken, each)) ?? rows.find((each) => each.allowed)
}
const isFirst = (row: QueueRow, rows: readonly QueueRow[]): boolean => !subtitles.value && headOf(rows) === row

const markOf = (row: QueueRow, rows: readonly QueueRow[]): string => !row.allowed ? t("output.queue.skipped")
  : isFirst(row, rows) ? t(props.queue === "reference" ? "output.queue.reference"
    : props.queue === "markup" ? "output.queue.markup" : "output.queue.taken") : ""
const isHeld = (row: QueueRow): boolean => isVariantOf(props.held, row)
// A queue of voice-overs is one group's: its rows are named by source and player alone.
const failureOf = (row: QueueRow): FailedAttempt | undefined => props.failed.find((each) =>
  each.held.source_id === row.source_id && each.held.player === row.player && (props.queue === "voice" || row.group === each.held.group))
const height = (row: QueueRow): string => row.height == null ? "—"
  : t(props.level ? "output.heightUpTo" : "output.height", { height: row.height })
const levelOf = (row: QueueRow): string => row.allowed_level === "default" ? "" : t(`output.level.${row.allowed_level}`)
// How the track of a variant of subtitles lies in the container (DESIGN.md 7.1.1): the output decides it, not the source. A
// container shows one track by itself: the default one taken from another row goes with the press.
function setDefault(row: QueueRow, on: boolean): void {
  if (on) for (const other of props.rows) if (other !== row && other.default) emit("allow", other, false, "default")
  emit("allow", row, on, "default")
}
const flagLevel = (level: QueueRow["default_level"]): string => level === "default" ? "" : t(`output.level.${level}`)

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
            <template v-if="subtitles">
              <th>{{ t("output.subtitles.name") }}</th>
              <th>{{ t("output.subtitles.language") }}</th>
              <th>{{ t("output.subtitles.type") }}</th>
              <th class="queue-flag" :title="t('output.subtitles.defaultHint')">{{ t("output.subtitles.default") }}</th>
              <th class="queue-flag" :title="t('output.subtitles.forcedHint')">{{ t("output.subtitles.forced") }}</th>
            </template>
            <template v-else>
              <th>{{ t("output.queue.player") }}</th>
              <th>{{ t("output.queue.height") }}</th>
              <th v-if="queue !== 'voice'">{{ t("output.queue.group") }}</th>
            </template>
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
                <input type="checkbox" data-lever-free :checked="row.allowed" :disabled="busy" :aria-label="t('output.queue.take')"
                       @change="emit('allow', row, ($event.target as HTMLInputElement).checked, 'allowed')" />
              </td>
              <td class="queue-handle">
                <button type="button" class="order-handle" :disabled="busy" :aria-label="t('output.queue.drag')" @keydown="step">
                  <i class="pi pi-bars" aria-hidden="true" />
                </button>
              </td>
              <td><span class="queue-source"><SourceMark :mark="marks[row.source_id]" /><span class="queue-place">{{ marks[row.source_id]?.place }}</span></span></td>
              <template v-if="subtitles">
                <td class="subtitle-name">{{ row.group }}</td>
                <td>{{ row.language || "—" }}</td>
                <td>{{ row.subtitle_type ? t(`output.subtitles.kind.${row.subtitle_type}` as MessageKey) : "—" }}</td>
                <td class="queue-flag">
                  <input type="checkbox" class="subtitle-default" data-lever-free :checked="row.default" :disabled="busy || !row.allowed"
                         :aria-label="t('output.subtitles.defaultOf', { name: row.group })"
                         :title="flagLevel(row.default_level) || undefined"
                         @change="setDefault(row, ($event.target as HTMLInputElement).checked)" />
                </td>
                <td class="queue-flag">
                  <input type="checkbox" class="subtitle-forced" data-lever-free :checked="row.forced" :disabled="busy || !row.allowed"
                         :aria-label="t('output.subtitles.forcedOf', { name: row.group })"
                         :title="flagLevel(row.forced_level) || undefined"
                         @change="emit('allow', row, ($event.target as HTMLInputElement).checked, 'forced')" />
                </td>
              </template>
              <template v-else>
                <td class="queue-player">{{ row.player_label }}</td>
                <td>{{ height(row) }}</td>
                <td v-if="queue !== 'voice'">{{ row.group }}</td>
              </template>
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
                <span v-if="failureOf(row)" class="pill queue-failed" :class="failureOf(row)!.covered ? 'pill-secondary' : 'pill-danger'"
                      :title="t('output.queue.failedHint')">{{ t(`output.queue.failed.${failureOf(row)!.kind}`) }}</span>
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
