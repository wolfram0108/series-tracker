<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue"
import { t } from "../i18n"
import { freshState, shortDate } from "../lib/freshness"

// How current the data of a source is, as a dot in the head it stands in: green «от …» while its term holds, yellow
// «устарели с …» once it is over, grey «не проверялась» for a release never checked. The core says until when; the dot
// turns yellow at that moment by itself (the time is the subject of the wait).
const props = defineProps<{ at: string | null; until: string | null }>()
const now = ref(Date.now())
const state = computed(() => freshState(props.at, props.until, now.value))
let timer: ReturnType<typeof setTimeout> | undefined
watch(() => props.until, (until) => {
  clearTimeout(timer)
  now.value = Date.now()
  const left = until ? Date.parse(until) - now.value : NaN
  if (left > 0) timer = setTimeout(() => { now.value = Date.now() }, left)
}, { immediate: true })
onBeforeUnmount(() => clearTimeout(timer))
</script>

<template>
  <span class="fresh-mark" :data-fresh="state">
    <span class="fresh-dot" aria-hidden="true" />
    <template v-if="state === 'never'">{{ t("fresh.never") }}</template>
    <template v-else-if="state === 'stale'">{{ t("fresh.stale", { at: shortDate(until!) }) }}</template>
    <template v-else>{{ t("fresh.from", { at: shortDate(at!) }) }}</template>
  </span>
</template>

<style scoped>
.fresh-mark { display: inline-flex; align-items: center; gap: 0.4rem; font-size: 0.85rem; color: var(--text-muted); white-space: nowrap; }
.fresh-dot { inline-size: 0.5rem; block-size: 0.5rem; border-radius: 50%; background: var(--fresh-color); }
.fresh-mark[data-fresh="fresh"] { --fresh-color: var(--color-green-1); }
.fresh-mark[data-fresh="stale"] { --fresh-color: var(--color-yellow-1); color: var(--fresh-stale-ink); font-weight: 600; }
.fresh-mark[data-fresh="never"] { --fresh-color: var(--text-muted); }
</style>
