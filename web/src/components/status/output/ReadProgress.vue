<script setup lang="ts">
import { t } from "../../../i18n"
import type { SourceMark as Mark } from "../../../lib/output"
import SourceMark from "./SourceMark.vue"

// The reads of the pages of the sources of a series under way: one row a source, as a meter of a season is — the source by its
// mark and its place, the phase, how far the read is, its count. The speed of a read is its hint, not a line of its own.
export interface ReadRow {
  key: number | string
  mark: Mark | null
  place: string
  phase: string
  current: number
  total: number
  rate: number | null
}

defineProps<{ reads: ReadRow[] }>()

const share = (row: ReadRow): string => `${row.total > 0 ? Math.min(100, (100 * row.current) / row.total) : 0}%`
</script>

<template>
  <div v-if="reads.length" class="reads">
    <div v-for="row in reads" :key="row.key" class="read" :title="row.rate != null ? t('output.reading.rate', { rate: row.rate.toFixed(1) }) : undefined">
      <i class="pi pi-spin pi-spinner read-spin" aria-hidden="true" />
      <span class="read-place"><SourceMark v-if="row.mark" :mark="row.mark" />{{ row.place }}</span>
      <span class="read-phase">{{ row.phase }}</span>
      <span class="read-bar" :class="{ 'is-unknown': row.total <= 0 }"><i :style="{ inlineSize: share(row) }" /></span>
      <span class="read-count">{{ row.total > 0 ? t("output.reading.count", { current: row.current, total: row.total }) : "" }}</span>
    </div>
  </div>
</template>

<style scoped>
.reads {
  display: flex; flex-direction: column; gap: 0.35rem; padding: 0.45rem 0.8rem; border: 1px solid var(--border-color);
  border-radius: 10px; background: linear-gradient(135deg, var(--bg-light) 0%, var(--border-color) 100%);
}
.read { display: grid; grid-template-columns: 1rem minmax(7rem, 11rem) minmax(5rem, 10rem) minmax(4rem, 1fr) auto; align-items: center; gap: 0.5rem; font-size: 0.8rem; }
.read-spin { font-size: 0.8rem; color: var(--color-blue); }
.read-place { display: inline-flex; align-items: center; gap: 0.4rem; min-inline-size: 0; overflow-wrap: anywhere; }
.read-phase { color: var(--text-muted); font-size: 0.75rem; }
.read-bar { block-size: 0.5rem; border-radius: 4px; background: #dee2e6; overflow: hidden; }
.read-bar i { display: block; block-size: 100%; border-radius: 4px; background: var(--color-blue); }
.read-bar.is-unknown i { inline-size: 35% !important; opacity: 0.6; animation: read-wait 1.4s ease-in-out infinite alternate; will-change: transform; }
/* To 65% of the bar, told in the run's own width (35% of the bar), so the compositor moves it. */
@keyframes read-wait { from { transform: translateX(0); } to { transform: translateX(calc(100% * 65 / 35)); } }
.read-count { font-variant-numeric: tabular-nums; color: var(--color-gray-600); font-size: 0.75rem; white-space: nowrap; }
/* Where the room is short of a row (a phone) the phase and the bar go under the source and the count. */
@container fold-room (inline-size < 30rem) {
  .read { grid-template-columns: 1rem 1fr auto; row-gap: 0.15rem; }
  .read-phase { grid-column: 2 / 4; }
  .read-bar { grid-column: 2 / 3; }
  .read-count { grid-column: 3 / 4; }
}
</style>
