<script setup lang="ts">
import { computed } from "vue"
import { t } from "../../../i18n"
import type { SourceMark } from "../../../lib/output"

// The mark of a source in its series: its place in the list and its colour, the same on every tab of the status window.
const props = defineProps<{ mark: SourceMark | null | undefined }>()
const style = computed(() => ({ background: props.mark ? `var(--source-${props.mark.color % 16})` : "var(--text-muted)" }))
</script>

<template>
  <span class="figure-mark" :style="style" :title="mark ? t('output.source.mark', { n: mark.n }) : undefined">{{ mark?.n ?? "—" }}</span>
</template>
