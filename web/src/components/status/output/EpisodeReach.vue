<script setup lang="ts">
import { computed } from "vue"
import { t } from "../../../i18n"
import { reachOf } from "../../../lib/ranges"
import HoverNote from "../../HoverNote.vue"

// The episodes of a voice-over in its chip: from the first to the last, however many gaps lie between, so that the chip is one short
// line whatever the voice-over holds. Where there are gaps the rest — what is there and what is missing, the gaps worded as the
// sources word them — is behind the numbers (HoverNote).
const props = defineProps<{ episodes: string }>()
const reach = computed(() => reachOf(props.episodes))
</script>

<template>
  <HoverNote class="voice-order-episodes" :more="reach.gaps !== ''">
    {{ reach.span }}
    <template #more>
      <dl class="note-sheet">
        <dt>{{ t("output.voices.have") }}</dt>
        <dd>{{ episodes }}</dd>
        <dt>{{ t("output.voices.gaps") }}</dt>
        <dd>{{ reach.gaps }}</dd>
      </dl>
    </template>
  </HoverNote>
</template>

<style scoped>
.voice-order-episodes { flex: none; color: var(--text-muted); white-space: nowrap; }
</style>
