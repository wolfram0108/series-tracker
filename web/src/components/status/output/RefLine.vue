<script setup lang="ts">
import { t } from "../../../i18n"
import type { Badge } from "../../../lib/compilations"
import type { RefPart, SourceMark as Mark } from "../../../lib/output"
import SourceMark from "./SourceMark.vue"

// One variant of the line of where the picture of an episode is from (lib/output.ts: refVariants): the mark of a source,
// the letter of the compilation in its colour (the mark of its source when the episode stands in none), the words.
defineProps<{ parts: RefPart[]; marks: Record<number, Mark>; badge?: Badge }>()
</script>

<template>
  <template v-for="(part, index) in parts" :key="index">
    <SourceMark v-if="part.kind === 'mark'" :mark="marks[part.sourceId]" />
    <span
      v-else-if="part.kind === 'letter' && badge" class="figure-mark ref-letter" :style="{ background: `var(--compilation-${badge.color})` }"
      :title="t('output.compilation.badge', { letter: badge.letter })"
    >{{ badge.letter }}</span>
    <SourceMark v-else-if="part.kind === 'letter'" :mark="marks[part.sourceId]" />
    <span v-else class="ref-text" :class="`is-${part.kind}`">{{ part.kind === "height" ? t("output.height", { height: part.height }) : part.text }}</span>
  </template>
</template>

<style scoped>
.ref-text { white-space: nowrap; }
</style>
