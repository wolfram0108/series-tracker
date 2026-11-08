<script setup lang="ts">
import { computed } from "vue"
import { t } from "../../../i18n"
import type { SourceMark } from "../../../lib/output"

// The mark of a source in its series: its place in the list and its colour, the same on every tab of the status window.
// `online`: the mark stands for a row of a file, whose presence its corner tells (styles/pills.css .presence).
// Not given is not known (null): a boolean prop left out would be false — offline — to Vue.
const props = withDefaults(defineProps<{ mark: SourceMark | null | undefined; online?: boolean | null }>(), { online: null })
const presence = computed(() => props.online == null ? null : props.online ? "is-online" : "is-offline")
const title = computed(() => [props.mark ? t("output.source.mark", { n: props.mark.n }) : null,
  presence.value ? t(props.online ? "output.presence.online" : "output.presence.offline") : null].filter(Boolean).join(" · ") || undefined)
// The colour alone: the corner of presence is the image of the same background.
const style = computed(() => ({ backgroundColor: props.mark ? `var(--source-${props.mark.color % 16})` : "var(--text-muted)" }))
</script>

<template>
  <span class="figure-mark" :class="presence && ['presence', presence]" :style="style" :title="title">{{ mark?.n ?? "—" }}</span>
</template>
