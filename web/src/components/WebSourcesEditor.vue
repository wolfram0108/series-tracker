<script setup lang="ts">
import StGroup from "./StGroup.vue"
import StSelect from "./StSelect.vue"
import { t, type MessageKey } from "../i18n"
import type { SourceKey, SourceRow, SourceState } from "../lib/webSources"

// Sources of a web release: a switch and the facts of each player, and the season of a shared catalog to take
// episodes from. A change is an address the form parses again, so the provider owns what an address means.
defineProps<{ rows: SourceRow[]; busy: boolean; progress: { current: number; total: number } | null }>()
const emit = defineEmits<{ (e: "pick", address: string): void }>()

const NAME: Record<SourceKey, string> = { vk: "VK", allvideo: "AllVideo", vibix: "Vibix" }
const STATE: Record<SourceState, MessageKey> = {
  off: "add.sources.off",
  absent: "player.state.absent",
  pending: "player.state.pending",
  available: "player.state.available",
  unavailable: "player.state.unavailable",
  reading: "add.sources.reading",
  choose: "add.sources.choose",
}
// The catalog of a shared source is read, the episodes of a page player are walked through.
const stateText = (row: SourceRow) => t(row.key === "vibix" && row.state === "reading" ? "add.sources.readingCatalog" : STATE[row.state])

const seasonOptions = (row: SourceRow) => row.seasons.map((season) => ({
  value: season.url,
  label: t("add.sources.seasonOption", {
    name: season.name, coverage: t("add.source.coverage", { ranges: season.ranges, count: season.count }),
  }),
}))

// The switch shows what the provider reported; it moves only when the parsed address comes back.
function toggle(event: Event, row: SourceRow) {
  ;(event.target as HTMLInputElement).checked = row.enabled
  if (row.toggle) emit("pick", row.toggle)
}
</script>

<template>
  <div class="sources">
    <div v-for="row in rows" :key="row.key" class="source" :class="{ 'is-off': !row.enabled }">
      <div class="source-head">
        <input
          type="checkbox" class="form-switch-input" :checked="row.enabled" :disabled="busy"
          :aria-label="t('add.sources.use', { player: NAME[row.key] })" @change="toggle($event, row)"
        />
        <span class="source-name">{{ NAME[row.key] }}</span>
        <span v-if="row.count !== null" class="source-count">{{ t("add.sources.count", { count: row.count }) }}</span>
        <span v-else class="source-state" :class="`is-${row.state}`">
          <i v-if="row.state === 'reading'" class="spin"></i>{{ stateText(row) }}
        </span>
      </div>
      <div v-if="row.seasons.length > 1 && row.enabled" class="source-field">
        <span class="source-field-label">{{ t("add.sources.season") }}</span>
        <StGroup>
          <StSelect
            :model-value="row.selected" :options="seasonOptions(row)"
            :placeholder="t('add.sources.seasonPlaceholder')" @update:model-value="emit('pick', String($event))"
          />
        </StGroup>
      </div>
      <div v-if="row.groups.length" class="player-report source-resolution">
        <span v-for="group in row.groups" :key="group.label" class="player-report-item">
          <b>{{ group.label }}</b><span>{{ group.ranges }}</span>
        </span>
      </div>
      <div v-if="row.gaps.length" class="player-report source-resolution">
        <span v-for="gap in row.gaps" :key="gap.kind" class="player-report-item" :class="`is-${gap.kind}`">
          <b>{{ t(`add.sources.gap.${gap.kind}`) }}</b><span>{{ gap.ranges }}</span>
        </span>
      </div>
    </div>
    <div v-if="progress" class="scan-progress">{{ t("add.sources.progress", progress) }}</div>
  </div>
</template>

<style scoped>
.sources { display: flex; flex-direction: column; gap: 12px; }
.source {
  display: flex; flex-direction: column; gap: 10px; padding: 12px 14px;
  border: 1px solid var(--border-color); border-radius: var(--border-radius); background: var(--bg-white);
}
.source-head { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 12px; }
.source.is-off .source-name { opacity: 0.55; }
.source-name { font-weight: 600; color: #2c3e50; }
.source-count, .source-state { margin-left: auto; display: inline-flex; align-items: center; gap: 6px; font-size: 0.9rem; }
.source-count { font-weight: 500; color: var(--color-text); }
.source-state { color: var(--text-muted); }
.source-state.is-available { color: #146c43; }
.source-state.is-unavailable { color: #b02a37; }
.source-state.is-choose { color: #8a6500; }
.source-state.is-reading { color: #0a7c8f; }
.source-field { display: flex; flex-direction: column; gap: 6px; }
.source-field-label { font-size: 0.85rem; font-weight: 500; color: var(--color-gray-600); }
.source-resolution { margin-top: 0; }
.player-report-item.is-unavailable span { color: #b02a37; }
.player-report-item.is-notObtained span { color: #8a6500; }
.spin {
  width: 11px; height: 11px; border: 2px solid currentColor; border-right-color: transparent;
  border-radius: 50%; animation: spin 0.8s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) { .spin { animation: none; } }
</style>
