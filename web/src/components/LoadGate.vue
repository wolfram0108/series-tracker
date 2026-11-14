<script setup lang="ts">
import Button from "primevue/button"
import Skeleton from "primevue/skeleton"
import type { LoadState } from "../composables/useLoad"
import { t } from "../i18n"

// A screen that waits for what it shows: the form when the read is done, the skeleton of it while it is going on (the same rows and
// the same height of a field, so nothing moves when the data comes), the word and a button where it could not be read. What stands
// in the slot is built only when `loaded`, so there is nothing to write into before then.
// `rows` is the number of rows of the sheet the skeleton draws; `fields` the fields in a row.
const props = withDefaults(defineProps<{ state: LoadState; rows?: number; fields?: number }>(), { rows: 3, fields: 2 })
defineEmits<{ retry: [] }>()
const bars = (row: number): number => (row % 2 === 0 ? props.fields : 1)
</script>

<template>
  <slot v-if="state === 'loaded'" />
  <div v-else-if="state === 'failed'" class="load-failed" role="alert">
    <span>{{ t("load.failed") }}</span>
    <Button :label="t('common.retry')" icon="pi pi-refresh" severity="secondary" @click="$emit('retry')" />
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
