<script setup lang="ts">
import draggable from "vuedraggable"
import { t } from "../../i18n"
import {
  allowMove,
  carriesAttachment,
  isDanglingOr,
  joinsNext,
  joinsPrevious,
  normalizeAttachments,
  removeBlockAt,
  type PatternBlock,
} from "../../lib/patternBlocks"
import { blockLabel } from "../../lib/ruleVocabulary"
import PatternPalette from "./PatternPalette.vue"

export type { PatternBlock }

// The editor of a pattern: the container of its blocks (reordered by drag and drop) and the palette (PatternPalette,
// a clone on a drag). One for the conditions «ЕСЛИ» and the actions «ТО». The blocks change in place (vuedraggable :list
// by the parent's reference). The logic came from the old static/js/components/settingsParser.js.

const props = withDefaults(defineProps<{
  blocks: PatternBlock[]
  context: "if" | "then"
  /** The palette under the blocks; a rule table has one palette of its own for all its cells instead. */
  showPalette?: boolean
}>(), { showPalette: true })

function isBlockEditable(block: PatternBlock): boolean {
  return ["text", "add", "subtract", "range"].includes(block.type)
}

function getBlockClasses(block: PatternBlock, index: number): string {
  const classes = ["pattern-block"]
  if (["add", "subtract"].includes(block.type)) classes.push("block-type-operation")
  else if (block.type === "text") classes.push("block-type-text")
  else classes.push(`block-type-${block.type}`)
  // Glued units: the suffix touches the number it modifies, the block or touches the texts it joins.
  if (joinsPrevious(props.blocks, index)) classes.push("is-glued")
  if (joinsNext(props.blocks, index)) classes.push("carries-attachment")
  if (isDanglingOr(props.blocks, index)) classes.push("is-dangling")
  return classes.join(" ")
}

function getBlockDisplayText(block: PatternBlock): string {
  if (isBlockEditable(block)) return block.value ?? ""
  if (block.type === "number" && props.context === "then") {
    const numberBlocks = props.blocks.filter((b) => b.type === "number")
    const captureIndex = numberBlocks.indexOf(block) + 1
    return t("rules.block.numberN", { n: captureIndex })
  }
  return blockLabel(block.type)
}

function updateBlockValue(block: PatternBlock, newText: string): void {
  if (!block) return
  const processed = newText.trim()
  if (block.type === "add" || block.type === "subtract") {
    const intValue = parseInt(processed, 10)
    block.value = isNaN(intValue) ? "0" : String(intValue)
  } else if (block.type === "range") {
    block.value = processed || "1, 3-10"
  } else {
    block.value = processed
  }
}

function removeBlock(index: number): void {
  // The sortable change event does not fire for a programmatic removal.
  removeBlockAt(props.blocks, index)
}

</script>

<template>
  <div class="pattern-constructor">
    <draggable
      :list="blocks"
      class="pattern-blocks-container"
      group="blocks"
      handle=".drag-handle"
      item-key="id"
      ghost-class="drag-ghost"
      :animation="200"
      :data-context="context"
      :move="allowMove"
      @change="normalizeAttachments(blocks)"
    >
      <template #item="{ element, index }">
        <div :class="getBlockClasses(element, index)"
             :title="isDanglingOr(props.blocks, index) ? t('rules.block.or.dangling') : undefined">
          <span class="drag-handle">
            <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" fill="currentColor" viewBox="0 0 16 16">
              <path d="M7 2a1 1 0 1 1-2 0 1 1 0 0 1 2 0zm3 0a1 1 0 1 1-2 0 1 1 0 0 1 2 0zM7 5a1 1 0 1 1-2 0 1 1 0 0 1 2 0zm3 0a1 1 0 1 1-2 0 1 1 0 0 1 2 0zM7 8a1 1 0 1 1-2 0 1 1 0 0 1 2 0zm3 0a1 1 0 1 1-2 0 1 1 0 0 1 2 0zm-3 3a1 1 0 1 1-2 0 1 1 0 0 1 2 0zm3 0a1 1 0 1 1-2 0 1 1 0 0 1 2 0z" />
            </svg>
          </span>
          <template v-if="['add', 'subtract'].includes(element.type)">
            <span class="operation-sign">{{ element.type === 'add' ? '+' : '-' }}</span>
            <div
              contenteditable="true"
              class="pattern-block-input operation-value"
              @blur="updateBlockValue(element, ($event.target as HTMLElement).innerText)"
              @keydown.enter.prevent
            >{{ element.value }}</div>
          </template>
          <template v-else-if="element.type === 'range'">
            <span class="range-prefix">{{ t("rules.block.rangePrefix") }}</span>
            <div
              contenteditable="true"
              class="pattern-block-input range-value"
              @blur="updateBlockValue(element, ($event.target as HTMLElement).innerText)"
              @keydown.enter.prevent="($event.target as HTMLElement).blur()"
            >{{ element.value || '1, 3-10' }}</div>
          </template>
          <template v-else>
            <div
              :contenteditable="isBlockEditable(element)"
              class="pattern-block-input"
              @blur="isBlockEditable(element) ? updateBlockValue(element, ($event.target as HTMLElement).innerText) : null"
              @keydown.enter.prevent="($event.target as HTMLElement).blur()"
            >{{ getBlockDisplayText(element) }}</div>
          </template>
          <button
            class="pattern-block-remove"
            :class="{ 'is-disabled': carriesAttachment(props.blocks, index) }"
            :disabled="carriesAttachment(props.blocks, index)"
            :title="carriesAttachment(props.blocks, index)
              ? t('rules.block.removeAttachmentFirst')
              : t('rules.block.remove')"
            @click="removeBlock(index)"
          />
        </div>
      </template>
    </draggable>
    <div v-if="showPalette" class="palette-footer">
      <PatternPalette :blocks="blocks" :context="context" />
    </div>
  </div>
</template>
