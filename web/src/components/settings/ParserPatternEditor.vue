<script setup lang="ts">
import { computed } from "vue"
import draggable from "vuedraggable"

// Конструктор паттерна: контейнер блоков (DnD-перестановка) + палитра
// (клон при перетаскивании). Общий для условий «ЕСЛИ» и действий «ТО».
// Блоки мутируются на месте (vuedraggable :list по ссылке родителя).
// Порт логики из static/js/components/settingsParser.js (cloneBlock,
// isBlockEditable, getBlockDisplayText, updateBlockValue, getBlockClasses).

export interface PatternBlock {
  id: number | string
  type: string
  value?: string
}
interface PaletteItem { type: string; label: string; title: string }

const props = defineProps<{
  blocks: PatternBlock[]
  context: "if" | "then"
}>()

const BLOCK_PALETTE: PaletteItem[] = [
  { type: "text", label: "Текст", title: "Точный текст" },
  { type: "number", label: "Число", title: "Любое число (1, 2, 10, 155...)" },
  { type: "add", label: "+", title: "Сложение" },
  { type: "subtract", label: "-", title: "Вычитание" },
  { type: "whitespace", label: "Пробел", title: "Один или несколько пробелов" },
  { type: "any_text", label: "*", title: "Любой текст (нежадный поиск)" },
  { type: "start_of_line", label: "Начало", title: "Соответствует началу названия" },
  { type: "end_of_line", label: "Конец", title: "Соответствует концу названия" },
]

// «ЕСЛИ» — без математических операций; «ТО» — все блоки.
const palette = computed(() =>
  props.context === "if"
    ? BLOCK_PALETTE.filter((b) => !["add", "subtract"].includes(b.type))
    : BLOCK_PALETTE,
)

function cloneBlock(original: PaletteItem): PatternBlock {
  const newBlock: PatternBlock = { id: Date.now() + Math.random(), type: original.type }
  if (original.type === "text") newBlock.value = ""
  if (original.type === "add" || original.type === "subtract") newBlock.value = "1"
  return newBlock
}

function isBlockEditable(block: PatternBlock): boolean {
  return ["text", "add", "subtract"].includes(block.type)
}

function getBlockClasses(block: PatternBlock): string {
  const classes = ["pattern-block"]
  if (["add", "subtract"].includes(block.type)) classes.push("block-type-operation")
  else if (block.type === "text") classes.push("block-type-text")
  else classes.push(`block-type-${block.type}`)
  return classes.join(" ")
}

function getBlockDisplayText(block: PatternBlock): string {
  if (isBlockEditable(block)) return block.value ?? ""
  if (block.type === "number" && props.context === "then") {
    const numberBlocks = props.blocks.filter((b) => b.type === "number")
    const captureIndex = numberBlocks.indexOf(block) + 1
    return `Число #${captureIndex}`
  }
  return BLOCK_PALETTE.find((p) => p.type === block.type)?.label || "Неизвестный"
}

function updateBlockValue(block: PatternBlock, newText: string): void {
  if (!block) return
  const processed = newText.trim()
  if (block.type === "add" || block.type === "subtract") {
    const intValue = parseInt(processed, 10)
    block.value = isNaN(intValue) ? "0" : String(intValue)
  } else {
    block.value = processed
  }
}

function removeBlock(index: number): void {
  props.blocks.splice(index, 1)
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
      ghost-class="ghost-block"
      :animation="200"
    >
      <template #item="{ element, index }">
        <div :class="getBlockClasses(element)">
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
          <template v-else>
            <div
              :contenteditable="isBlockEditable(element)"
              class="pattern-block-input"
              @blur="isBlockEditable(element) ? updateBlockValue(element, ($event.target as HTMLElement).innerText) : null"
              @keydown.enter.prevent
            >{{ getBlockDisplayText(element) }}</div>
          </template>
          <button class="pattern-block-remove" title="Удалить блок" @click="removeBlock(index)">&times;</button>
        </div>
      </template>
    </draggable>
    <div class="palette-footer">
      <draggable
        :list="palette"
        class="pattern-palette"
        :group="{ name: 'blocks', pull: 'clone', put: false }"
        :clone="cloneBlock"
        item-key="type"
        :sort="false"
      >
        <template #item="{ element }">
          <div :class="['palette-btn', 'block-type-' + element.type]" :title="element.title">{{ element.label }}</div>
        </template>
      </draggable>
    </div>
  </div>
</template>
