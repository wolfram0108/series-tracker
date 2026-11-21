<script setup lang="ts">
import type { ShownCells } from "../composables/useShownCells"

// One cell of a list drawn only near the view (composables/useShownCells.ts): its card while it is near or kept, its shell
// otherwise. The cell decides it by itself, so a card that comes or goes as the list scrolls redraws its cell alone, not the
// list of every card.
defineProps<{ cells: ShownCells; cellKey: string | number; keep?: boolean }>()
</script>

<template>
  <slot v-if="keep || cells.built(cellKey)" />
  <slot v-else name="shell" :size="cells.sizeOf(cellKey)" />
</template>
