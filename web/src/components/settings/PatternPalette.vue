<script setup lang="ts">
import { computed } from "vue"
import draggable from "vuedraggable"
import { allowMove, type PatternBlock } from "../../lib/patternBlocks"
import { cloneBlock, paletteFor, type PaletteItem } from "../../lib/ruleVocabulary"

// The palette of the blocks of a template: a block is dragged into a template, or picked (a click) into the template
// the palette serves. What the template cannot take is offered disabled, with its reason.
const props = defineProps<{
  /** The template the palette serves: its blocks decide what is disabled; none, and a pick goes nowhere. */
  blocks: PatternBlock[] | null
  context: "if" | "then"
}>()
const emit = defineEmits<{ pick: [block: PatternBlock] }>()

const items = computed(() => paletteFor(props.blocks ?? [], props.context))

function pick(item: PaletteItem): void {
  if (!item.disabled && props.blocks) emit("pick", cloneBlock(item))
}
</script>

<template>
  <draggable
    :list="items"
    class="pattern-palette"
    :group="{ name: 'blocks', pull: 'clone', put: false }"
    :clone="cloneBlock"
    item-key="type"
    :sort="false"
    :filter="'.palette-btn.is-disabled'"
    :move="allowMove"
  >
    <template #item="{ element }">
      <div
        :class="['palette-btn', 'block-type-' + element.type, { 'is-disabled': element.disabled }]"
        :title="element.disabled ? element.reason : element.title"
        @click="pick(element)"
      >{{ element.label }}</div>
    </template>
  </draggable>
</template>
