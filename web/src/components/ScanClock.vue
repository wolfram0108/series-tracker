<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue"
import { scanClock } from "../lib/scanClock"
import { useScannerStore } from "../stores/scanner"

// The time left to the scheduled scan, in the header. The status comes from the core (SSE); the clock moves the words only
// at the moment they change (the next whole minute), the countdown being the subject itself.
const scanner = useScannerStore()
const now = ref(Date.now())
const shown = computed(() => scanClock(scanner.status, now.value))
let timer: ReturnType<typeof setTimeout> | undefined

watch(() => shown.value?.changesIn, (changesIn) => {
  clearTimeout(timer)
  if (changesIn != null) timer = setTimeout(() => { now.value = Date.now() }, changesIn)
}, { immediate: true })
// A new status may set another time: the clock is read again with it.
watch(() => scanner.status, () => { now.value = Date.now() })
onBeforeUnmount(() => clearTimeout(timer))
</script>

<template>
  <span v-if="shown" class="scan-clock" :title="shown.at || undefined">{{ shown.text }}</span>
</template>
