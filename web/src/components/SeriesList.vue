<script setup lang="ts">
import { ref } from "vue"
import SeriesCard from "./SeriesCard.vue"
import ShownCell from "./ShownCell.vue"
import { useShownCells } from "../composables/useShownCells"
import { useCoveredBlur } from "../composables/useWindowCover"
import { useSeriesStore } from "../stores/series"

// The list of the series of the main screen, as the application draws it and as the page of its load draws it
// (tools/LoadSim.vue). A card is drawn while it is near the view (composables/useShownCells.ts); under an open window its
// labels do not blur their backdrop, since they are not seen there (composables/useWindowCover.ts).
const emit = defineEmits<{
  (e: "open-status", id: number): void
  (e: "scan", id: number): void
  (e: "cancel-scan", id: number): void
  (e: "delete", id: number): void
  (e: "toggle-auto-scan", id: number, enabled: boolean): void
}>()

const seriesStore = useSeriesStore()
const list = ref<HTMLElement | null>(null)
const shown = useShownCells(list)
const { cell } = shown
useCoveredBlur(list)
</script>

<template>
  <div ref="list" class="series-list">
    <ShownCell v-for="s in seriesStore.list" :key="s.id" :cells="shown" :cell-key="s.id">
      <template #shell="{ size }"><div :ref="cell(s.id)" class="cell-shell series-shell" :style="size" aria-hidden="true" /></template>
      <SeriesCard
        :ref="cell(s.id)"
        class="cell-in"
        :series="s"
        :saving="seriesStore.isSaving(s.id)"
        @open-status="emit('open-status', $event)"
        @scan="emit('scan', $event)"
        @cancel-scan="emit('cancel-scan', $event)"
        @delete="emit('delete', $event)"
        @toggle-auto-scan="(id, enabled) => emit('toggle-auto-scan', id, enabled)"
      />
    </ShownCell>
    <slot />
  </div>
</template>
