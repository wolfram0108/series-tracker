<script setup lang="ts">
import { computed } from "vue"
import { t, type MessageKey } from "../../../i18n"
import { outputLines, type EpisodeView, type SourceMark as Mark, type TrackCell, type TrackState } from "../../../lib/output"
import { reasonText } from "../../../lib/reason"
import SourceMark from "./SourceMark.vue"

// The tracks of an episode, each with where it comes from, its state and the action that fits it: the reference is
// downloaded again, a placed voice-over made anew, a failed one done again. An action is the one command it names.
const props = withDefaults(defineProps<{ view: EpisodeView; cells: TrackCell[]; marks: Record<number, Mark>; busy?: boolean }>(),
  { busy: false })
const emit = defineEmits<{
  (e: "refetch", part: number | null): void
  (e: "retry", group: string): void
  (e: "redo", group: string): void
}>()

// A voice-over no source has a line of is not helped by trying again.
const NO_SOURCE = new Set(["no_source", "not_in_any_source"])
// The track of the reference is the file of the video: it is worded as the files of the plan are.
const FILE_WORDS: Record<TrackState, MessageKey> = {
  done: "output.file.status.completed", down: "output.file.status.downloading", align: "output.file.status.processing",
  wait: "output.file.status.pending", error: "output.file.status.error", none: "output.file.status.none",
}
const stateText = (cell: TrackCell): string => t(cell.isReference ? FILE_WORDS[cell.state] : `output.track.state.${cell.state}`)
const PILL: Record<TrackState, string> = {
  done: "pill-success", down: "pill-primary", align: "pill-primary", wait: "pill-secondary", error: "pill-danger", none: "pill-secondary",
}

type Action = "refetch" | "retry" | "redo" | null

const rows = computed(() => {
  const lines = outputLines(props.view)
  return props.cells.map((cell) => {
    const line = lines.find((each) => each.group === cell.group && each.kind === (cell.isReference ? "reference" : "dub"))
    const sourceId = cell.isReference
      ? props.view.references.find((each) => (each.voice_group ?? "") === cell.group)?.source_id ?? null
      : line?.dub?.source_id ?? null
    const height = line?.height == null ? "" : ` ${t("output.height", { height: line.height })}`
    const detail = line?.dub?.detail ?? null
    const action: Action = cell.isReference ? (cell.state === "done" ? "refetch" : null)
      : cell.state === "done" ? "redo"
      : cell.state === "error" && !(detail && NO_SOURCE.has(detail.code)) ? "retry" : null
    return {
      cell, sourceId, part: line?.part ?? null,
      where: line && (line.playerLabel ?? line.player) ? `${line.playerLabel ?? line.player}${height}` : "",
      note: detail ? reasonText(detail) : "", action,
    }
  })
})

function act(row: (typeof rows.value)[number]): void {
  if (row.action === "refetch") emit("refetch", row.part)
  else if (row.action === "retry") emit("retry", row.cell.group)
  else if (row.action === "redo") emit("redo", row.cell.group)
}
</script>

<template>
  <table class="track-table">
    <thead>
      <tr>
        <th class="narrow">{{ t("output.tracks.place") }}</th>
        <th>{{ t("output.tracks.track") }}</th>
        <th>{{ t("output.tracks.from") }}</th>
        <th>{{ t("output.tracks.status") }}</th>
        <th class="narrow">{{ t("output.tracks.actions") }}</th>
      </tr>
    </thead>
    <tbody>
      <tr v-for="row in rows" :key="row.cell.pos" :data-group="row.cell.group">
        <td><span class="track-place">{{ row.cell.pos }}</span></td>
        <td>
          <b>{{ row.cell.group }}</b>
          <span v-if="row.cell.isReference" class="pill pill-primary track-ref">{{ t("output.tracks.reference") }}</span>
        </td>
        <td>
          <span v-if="row.where" class="track-from"><SourceMark :mark="row.sourceId === null ? null : marks[row.sourceId]" />{{ row.where }}</span>
          <span v-else class="muted">—</span>
        </td>
        <td>
          <span class="pill" :class="PILL[row.cell.state]">{{ stateText(row.cell) }}</span>
          <span v-if="row.note" class="track-note">{{ row.note }}</span>
        </td>
        <td class="narrow">
          <button v-if="row.action" type="button" class="track-action" :class="`do-${row.action}`" :disabled="busy" @click="act(row)">
            {{ t(`output.action.${row.action}`) }}
          </button>
          <span v-else class="muted">—</span>
        </td>
      </tr>
    </tbody>
  </table>
</template>

<style scoped>
.track-table { inline-size: 100%; border-collapse: collapse; border: 1px solid var(--border-color); font-size: 0.85rem; }
.track-table th {
  background: var(--bg-light); font-weight: 700; text-align: start; padding: 0.5rem 0.6rem;
  border-bottom: 1px solid var(--border-color); white-space: nowrap;
}
.track-table td { padding: 0.4rem 0.6rem; border-bottom: 1px solid var(--border-color); vertical-align: middle; }
.track-table tbody tr:nth-child(odd) { background: rgba(0, 0, 0, 0.02); }
.narrow { inline-size: 1%; white-space: nowrap; }
.track-place {
  display: inline-grid; place-items: center; inline-size: 1.25rem; block-size: 1.25rem; border-radius: 6px;
  background: var(--color-gray-600); color: #fff; font-size: 0.75rem; font-weight: 700;
}
.track-ref { margin-inline-start: 0.4rem; }
.track-from { display: inline-flex; align-items: center; gap: 0.4rem; }
.track-note { margin-inline-start: 0.4rem; color: var(--text-muted); }
.muted { color: var(--text-muted); }
.track-action {
  border: 1px solid var(--border-color); background: var(--bg-white); border-radius: 8px; padding: 0.25rem 0.6rem;
  cursor: pointer; color: var(--color-gray-600); font: inherit;
}
.track-action:disabled { opacity: 0.5; cursor: default; }
</style>
