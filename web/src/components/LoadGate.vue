<script setup lang="ts">
import Button from "primevue/button"
import Skeleton from "primevue/skeleton"
import type { LoadState } from "../composables/useLoad"
import { useBusyBar } from "../composables/useWindowBusy"
import { t } from "../i18n"

// A screen that waits for what it shows: the form when the read is done, the skeleton of it while it is going on (the same rows and
// the same height of a field, so nothing moves when the data comes), the word and a button where it could not be read. What stands
// in the slot is built only when `loaded`, so there is nothing to write into before then. The window it stands in is told that it is
// reading (the bar under its title, composables/useWindowBusy.ts).
// `layout`: the skeleton of a sheet of fields (`rows` rows of `fields` fields), or of a table of cards (a season of them).
const props = withDefaults(defineProps<{ state: LoadState; layout?: "sheet" | "cards"; rows?: number; fields?: number }>(),
  { layout: "sheet", rows: 3, fields: 2 })
defineEmits<{ retry: [] }>()
useBusyBar(() => props.state === "loading")
const bars = (row: number): number => (row % 2 === 0 ? props.fields : 1)
</script>

<template>
  <slot v-if="state === 'loaded'" />
  <div v-else-if="state === 'failed'" class="load-failed" role="alert">
    <span>{{ t("load.failed") }}</span>
    <Button :label="t('common.retry')" icon="pi pi-refresh" severity="secondary" @click="$emit('retry')" />
  </div>
  <div v-else-if="layout === 'cards'" class="load-skeleton load-cards" role="status" aria-busy="true" :aria-label="t('common.fetching')">
    <Skeleton class="load-caption" width="9rem" height="1rem" />
    <div class="load-card-grid">
      <Skeleton v-for="card in 8" :key="card" class="load-card" height="6.5rem" border-radius="var(--border-radius)" />
    </div>
  </div>
  <div v-else class="sheet load-skeleton" role="status" aria-busy="true" :aria-label="t('common.fetching')">
    <div v-for="row in rows" :key="row" class="sheet-row">
      <Skeleton class="load-caption" width="7rem" height="0.75rem" />
      <div class="sheet-pair">
        <Skeleton v-for="field in bars(row)" :key="field" class="load-field" height="2.875rem" border-radius="var(--border-radius)" /> <!-- the height of a field of a sheet -->
      </div>
    </div>
  </div>
</template>
