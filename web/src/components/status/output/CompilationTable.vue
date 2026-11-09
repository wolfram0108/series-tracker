<script setup lang="ts">
import { t } from "../../../i18n"
import type { Badge, CompilationView, CutState } from "../../../lib/compilations"
import type { SourceMark as Mark } from "../../../lib/output"
import PowerSwitch from "./PowerSwitch.vue"
import SourceMark from "./SourceMark.vue"

/** A compilation that covers the episode, as its row shows it. */
export interface CompilationRow {
  view: CompilationView
  badge: Badge
  member: boolean
  /** The episode may be put into it: it is switched on and better than the episode's own single video. */
  fits: boolean
  player: string
  cut: CutState | null
  /** The share of the video of the cut taken from the compilation, 0…1. */
  share: number | null
}

// The compilations that cover an episode (stage 12): the episode stands in one of them, by the same switch as on its card;
// switching another on takes it out of the one it was in. A compilation no better than the episode's own video cannot take it.
defineProps<{ rows: CompilationRow[]; marks: Record<number, Mark>; busy?: boolean }>()
const emit = defineEmits<{ (e: "member", key: string): void; (e: "retry"): void }>()

const range = (row: CompilationRow) => `${row.view.ref.start}–${row.view.ref.end}`
const CUT_PILL: Record<CutState, string> = {
  wait: "pill-secondary", down: "pill-primary", align: "pill-primary", ready: "pill-success", error: "pill-danger",
}
</script>

<template>
  <div class="queue-panel compilation-panel">
    <div class="queue-tools"><span>{{ t("output.compilation.hint") }}</span></div>
    <table class="queue-table compilation-table">
      <thead>
        <tr>
          <th class="queue-take">{{ t("output.compilation.table.take") }}</th>
          <th>{{ t("output.compilation.table.source") }}</th>
          <th>{{ t("output.compilation.table.player") }}</th>
          <th>{{ t("output.compilation.table.height") }}</th>
          <th>{{ t("output.compilation.table.episodes") }}</th>
          <th>{{ t("output.compilation.table.cut") }}</th>
          <th>{{ t("output.compilation.table.share") }}</th>
          <th />
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="row.view.ref.key" class="variant-row"
            :class="{ 'is-first': row.member, 'is-skipped': !row.fits }" :data-compilation="row.view.ref.key">
          <td class="queue-take">
            <PowerSwitch :on="row.member" :busy="busy || !row.fits"
                         :label="t(row.member ? 'output.compilation.leave' : 'output.compilation.take')"
                         @toggle="emit('member', $event ? row.view.ref.key : '')" />
          </td>
          <td>
            <span class="queue-source">
              <span class="glass card-flag compilation-letter" :style="{ '--tone': `var(--compilation-${row.badge.color})` }">{{ row.badge.letter }}</span>
              <SourceMark :mark="marks[row.view.ref.source_id]" />
              <span class="queue-place">{{ marks[row.view.ref.source_id]?.place }}</span>
            </span>
          </td>
          <td class="queue-player">{{ row.player }}</td>
          <td>{{ row.view.ref.height ? t("output.height", { height: row.view.ref.height }) : "—" }}</td>
          <td class="compilation-range">{{ range(row) }}</td>
          <td>
            <template v-if="row.member && row.cut">
              <span class="pill" :class="CUT_PILL[row.cut]">{{ t(`output.cut.${row.cut}`) }}</span>
              <button v-if="row.cut === 'error'" type="button" class="action-btn btn-scan do-retry-cut" data-lever :disabled="busy"
                      @click="emit('retry')">{{ t("output.cut.retry") }}</button>
            </template>
            <span v-else class="muted">—</span>
          </td>
          <td>{{ row.member && row.share !== null ? t("output.compilation.share", { n: Math.round(row.share * 100) }) : "—" }}</td>
          <td class="queue-mark-cell">
            <span class="queue-mark">
              <span v-if="row.member" class="pill pill-primary">{{ t("output.compilation.ref1") }}</span>
              <span v-else-if="!row.fits" class="queue-level">{{ t("output.compilation.absence.not_better") }}</span>
            </span>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<style scoped>
.compilation-range { white-space: nowrap; font-variant-numeric: tabular-nums; }
.compilation-letter {
  display: inline-flex; justify-content: center; min-inline-size: 1.4rem; padding-inline: 0.3rem; border-radius: 0.35rem;
  background: var(--tone); color: #fff; font-weight: 700;
}
</style>
