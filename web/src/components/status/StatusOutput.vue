<script setup lang="ts">
import { t } from "../../i18n"

// Place of the "Output" section of a video series (references, voice-overs, levers). The section replaces the old
// "Composition" tab; until it is connected the tab says so instead of showing a layout built on removed data.
defineProps<{ seriesId: number; seriesName: string }>()
</script>

<template>
  <div class="status-output">
    <div class="empty-state">{{ t("output.stub") }}</div>
  </div>
</template>
