<script setup lang="ts">
import { computed } from "vue"

// A number in a coloured box: the place of a thing in its list (a rule of a profile, an action of «ТО»), coloured as the
// sources of the output are (16 colours, --source-0…15, by the place): one look for every numbered list of the window.
// `dim`: the number is not the thing's own but the one it takes (a row of a table with the action of its column).
const props = defineProps<{ n: number; title?: string; dim?: boolean }>()
const style = computed(() => ({ backgroundColor: `var(--source-${(Math.max(1, props.n) - 1) % 16})` }))
</script>

<template>
  <span class="figure-mark number-mark" :class="{ 'is-dim': dim }" :style="style" :title="title">{{ n }}</span>
</template>

<style scoped>
.number-mark.is-dim { opacity: 0.45; }
</style>
