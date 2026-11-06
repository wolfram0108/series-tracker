<script setup lang="ts">
import { t } from "../../../i18n"
import type { TrackCell } from "../../../lib/output"

// The tracks of an episode in the order of the container: the figure is the place of the track, the colour its state; the
// track of the reference (the voice of the video itself) is outlined. Cells narrow when a strip has no room, never wrap.
defineProps<{ cells: TrackCell[] }>()

const title = (cell: TrackCell): string => t(cell.isReference ? "output.track.cellReference" : "output.track.cell",
  { group: cell.group, state: t(`output.track.state.${cell.state}`) })
</script>

<template>
  <span class="track-strip">
    <span
      v-for="cell in cells" :key="cell.pos" class="track-cell" :class="[`is-${cell.state}`, { 'is-reference': cell.isReference }]"
      :title="title(cell)" :aria-label="title(cell)"
    >{{ cell.pos }}</span>
  </span>
</template>

<style scoped>
/* The strip keeps room around its cells for the outline of the track of the reference. */
.track-strip { display: flex; align-items: center; gap: 0.125rem; padding-inline: calc((var(--card-row) - var(--track-size)) / 2); max-inline-size: 100%; overflow: hidden; }
.track-cell {
  flex: 0 1 auto; inline-size: var(--figure-size); min-inline-size: 0.25rem; block-size: var(--track-size); overflow: hidden;
  display: grid; place-items: center; border-radius: calc(var(--track-size) * var(--round)); color: #fff; font-size: 0.625rem; font-weight: 700; line-height: 1;
}
.track-cell.is-done { background: #409444; }
.track-cell.is-down { background: #009688; }
.track-cell.is-align { background: var(--color-blue); }
.track-cell.is-wait { background: #ced4da; color: #343a40; }
.track-cell.is-error { background: var(--color-red-1); }
.track-cell.is-none { background: none; border: 1px dashed #adb5bd; color: #adb5bd; }
/* An outline is painted above the cells around it, where a shadow lies under the next one. */
.track-cell.is-reference { position: relative; z-index: 1; overflow: visible; outline: 0.125rem solid #2c3e50; outline-offset: 0.0625rem; }
</style>
