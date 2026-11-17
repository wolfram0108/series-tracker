<script setup lang="ts">
import { t } from "../../../i18n"
import type { TrackCell } from "../../../lib/output"
import type { SubtitleLine } from "../../../lib/plan"
import { stateWord, TRACK_STATES } from "../../../lib/states"

// The tracks of an episode in the order of the container: the figure is the place of the track, the colour its state; the
// track of the reference (the voice of the video itself) is outlined. Cells narrow when a strip has no room, never wrap.
// The subtitles of the file follow the sounds, apart from them (DESIGN.md 7.1.1), each by its number; filled when the file holds it.
withDefaults(defineProps<{ cells: TrackCell[]; subtitles?: SubtitleLine[] }>(), { subtitles: () => [] })

const title = (cell: TrackCell): string => [t(cell.isReference ? "output.track.cellReference" : "output.track.cell",
  { group: cell.group, state: stateWord(TRACK_STATES[cell.state]) }),
  ...(cell.online == null ? [] : [t(cell.online ? "output.presence.online" : "output.presence.offline")])].join(" · ")
</script>

<template>
  <span class="track-strip">
    <span
      v-for="cell in cells" :key="cell.pos" class="track-cell"
      :data-state="TRACK_STATES[cell.state]"
      :class="[{ 'is-reference': cell.isReference }, cell.online == null ? null : ['presence', cell.online ? 'is-online' : 'is-offline']]"
      :title="title(cell)" :aria-label="title(cell)"
    >{{ cell.pos }}</span>
    <template v-for="line in subtitles" :key="`s${line.n}`">
      <span v-if="line.n !== null" class="track-cell subtitle-cell" :class="{ 'is-held': line.state === 'done' }"
            :title="line.name" :aria-label="line.name">{{ t("output.subtitles.mark", { n: line.n }) }}</span>
    </template>
  </span>
</template>

<style scoped>
/* The strip keeps room around its cells for the outline of the track of the reference. */
.track-strip { display: flex; align-items: center; gap: 0.125rem; padding-inline: calc((var(--card-row) - var(--track-size)) / 2); max-inline-size: 100%; overflow: hidden; }
.track-cell {
  flex: 0 1 auto; inline-size: var(--figure-size); min-inline-size: 0.25rem; block-size: var(--track-size); overflow: hidden;
  display: grid; place-items: center; border-radius: calc(var(--track-size) * var(--round)); font-size: 0.625rem; font-weight: 700; line-height: 1;
  background-color: var(--tone-end); color: var(--tone-ink);
}
/* A track the episode has not (switched off, no row) is drawn by its outline, in the colour of its state. */
.track-cell[data-state="off"] { background: transparent; color: var(--tone); outline: 1px dashed var(--tone); }
.track-cell[data-state="noRow"] { background: none; border: 1px dashed var(--tone-end); color: var(--tone-end); }
/* An outline is painted above the cells around it, where a shadow lies under the next one. */
.subtitle-cell { inline-size: auto; padding-inline: 0.2rem; margin-inline-start: 0.125rem; background: var(--bg-light); color: var(--text-muted); }
.subtitle-cell.is-held { background: var(--subtitle-color); color: var(--bg-white); }
.track-cell.is-reference { position: relative; z-index: 1; overflow: visible; outline: 0.125rem solid #2c3e50; outline-offset: 0.0625rem; }
</style>
