<script setup lang="ts">
import { computed } from "vue"
import { t } from "../../../i18n"
import { outputLines, type EpisodeView, type SourceMark as Mark, type TrackCell } from "../../../lib/output"
import { reasonText } from "../../../lib/reason"
import SourceMark from "./SourceMark.vue"
import { stateLabel, TRACK_STATES } from "../../../lib/states"

// The tracks of an episode, each with where it comes from, its state and the action that fits it: the reference is
// downloaded again, a placed voice-over made anew, a failed one done again. An action is the one command it names.
const props = withDefaults(defineProps<{ view: EpisodeView; cells: TrackCell[]; marks: Record<number, Mark>; busy?: boolean }>(),
  { busy: false })
const emit = defineEmits<{
  (e: "refetch", part: number | null): void
  (e: "retry", group: string): void
  (e: "redo", group: string): void
}>()


type Action = "refetch" | "retry" | "redo" | "delete" | null

const rows = computed(() => {
  const lines = outputLines(props.view)
  return props.cells.map((cell) => {
    const line = lines.find((each) => each.group === cell.group && each.kind === (cell.isReference ? "reference" : "dub"))
    const sourceId = cell.isReference
      ? props.view.references.find((each) => (each.voice_group ?? "") === cell.group)?.source_id ?? null
      : line?.dub?.source_id ?? null
    const height = line?.height == null ? "" : ` ${t("output.height", { height: line.height })}`
    const detail = line?.dub?.detail ?? null
    // The button names what its command does (planner.delete): a row a source still gives is fetched or made again; a
    // row no source gives any more has nothing to come again from, so the command takes it out of the file.
    const gone = line?.online === false
    const action: Action = cell.state === "done" ? (gone ? "delete" : cell.isReference ? "refetch" : "redo")
      : !cell.isReference && cell.state === "error" ? "retry" : null
    // A placed track the service did not vouch for is done, and its pill bears the warning of that verdict.
    const pill = cell.state === "done" && detail?.code === "align_unsure" ? "pill-warning" : null
    return {
      cell, sourceId, part: line?.part ?? null,
      where: line && (line.playerLabel ?? line.player) ? `${line.playerLabel ?? line.player}${height}` : "",
      note: detail ? reasonText(detail) : "", action, pill,
      // The source forgot the row the video or the track came from: the file keeps it, with where it came from (an offline row).
      offline: line?.online === false,
      online: cell.state === "none" || cell.state === "absent" || cell.state === "off" ? null : line?.online ?? null,
    }
  })
})

function act(row: (typeof rows.value)[number]): void {
  // «Скачать заново», «Пересоздать» and «Удалить» are one command of the plan («Удалить» of the file or the track); the
  // button says what it comes to for this row.
  if (row.action === "retry") emit("retry", row.cell.group)
  else if (row.action && row.cell.isReference) emit("refetch", row.part)
  else if (row.action) emit("redo", row.cell.group)
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
        <td><span class="figure-mark">{{ row.cell.pos }}</span></td>
        <td>
          <b>{{ row.cell.group }}</b>
          <span v-if="row.cell.isReference" class="pill pill-primary track-ref">{{ t("output.tracks.reference") }}</span>
        </td>
        <td>
          <span v-if="row.where" class="track-from"><SourceMark :mark="row.sourceId === null ? null : marks[row.sourceId]" :online="row.online" />{{ row.where }}</span>
          <span v-else class="muted">—</span>
        </td>
        <td>
          <span class="pill" :class="row.pill" :data-state="row.pill ? undefined : TRACK_STATES[row.cell.state]">{{ stateLabel(TRACK_STATES[row.cell.state]) }}</span>
          <span v-if="row.offline" class="track-offline">{{ t("output.plan.offline") }}</span>
          <span v-if="row.note" class="track-note">{{ row.note }}</span>
        </td>
        <td class="narrow">
          <button v-if="row.action" type="button" class="track-action" data-lever :class="`do-${row.action}`" :disabled="busy" @click="act(row)">
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
.track-ref { margin-inline-start: 0.4rem; }
.track-from { display: inline-flex; align-items: center; gap: 0.4rem; }
.track-note { margin-inline-start: 0.4rem; color: var(--text-muted); }
.track-offline { margin-inline-start: 0.4rem; color: var(--text-muted); font-style: italic; }
.muted { color: var(--text-muted); }
.track-action {
  border: 1px solid var(--border-color); background: var(--bg-white); border-radius: 8px; padding: 0.25rem 0.6rem;
  cursor: pointer; color: var(--color-gray-600); font: inherit;
}
.track-action:disabled { opacity: 0.5; cursor: default; }
</style>
