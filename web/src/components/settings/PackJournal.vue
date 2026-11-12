<script setup lang="ts">
import { computed } from "vue"
import type { components } from "../../api/schema"
import { t, type MessageKey } from "../../i18n"
import { stateLabel } from "../../lib/states"

type Journal = components["schemas"]["PackJournal"]

// The journal of a series being carried out, as the core keeps it (core/contracts/packing.py): every file the sync puts in
// place, takes away, renames or rewrites, whichever episode's it is, one step after another.
const props = defineProps<{ journal: Journal }>()

const done = computed(() => props.journal.steps_done ?? 0)
const of = computed(() => props.journal.steps_of ?? 0)
const percent = computed(() => (of.value ? Math.round((done.value / of.value) * 100) : 0))
const duration = (seconds: number | null | undefined): string => {
  const whole = Math.max(0, Math.round(seconds ?? 0))
  const h = Math.floor(whole / 3600), m = Math.floor((whole % 3600) / 60), s = whole % 60
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`
}
const clock = (iso: string | undefined): string => (iso ? new Date(iso).toLocaleTimeString("ru-RU") : "")
</script>

<template>
  <div class="card-final card-queue pack-card work-card pack-journal is-running" :data-series="journal.series_id">
    <div class="queue-row work-head">
      <span class="queue-title work-title"><span class="work-series">#{{ journal.series_id }}</span>
        <span>{{ journal.series_name }} · {{ t(`pack.journal.kind.${journal.kind ?? "sync"}` as MessageKey) }}</span></span>
      <span class="pill" data-state="apply">{{ stateLabel("apply") }}</span>
    </div>
    <div class="work-facts">
      <span class="journal-count">{{ t("pack.journal.steps", { done, of }) }}</span>
    </div>
    <div class="progress">
      <div class="progress-bar progress-bar-striped progress-bar-animated" :style="{ width: `${percent}%` }">{{ percent }}%</div>
    </div>
    <div v-if="journal.step" class="work-facts">
      <span class="journal-step">{{ t(`pack.journal.step.${journal.step}` as MessageKey) }}: <b>{{ journal.file }}</b></span>
    </div>
    <div class="work-facts work-foot">
      <span v-if="journal.started_at">{{ t("pack.journal.since") }} <b>{{ clock(journal.started_at) }}</b></span>
      <span>{{ t("pack.journal.time") }} <b>{{ duration(journal.seconds) }}</b></span>
    </div>
  </div>
</template>
