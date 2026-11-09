<script setup lang="ts">
import { computed } from "vue"
import draggable from "vuedraggable"
import { t } from "../../i18n"
import {
  attachmentBlockCount,
  canDrop,
  carriesAttachment,
  isAttachmentType,
  isDanglingOr,
  joinsNext,
  joinsPrevious,
  normalizeAttachments,
  numberBlockCount,
  removeBlockAt,
  splitsAttachment,
  type PatternBlock,
} from "../../lib/patternBlocks"

export type { PatternBlock }

// Конструктор паттерна: контейнер блоков (DnD-перестановка) + палитра
// (клон при перетаскивании). Общий для условий «ЕСЛИ» и действий «ТО».
// Блоки мутируются на месте (vuedraggable :list по ссылке родителя).
// Порт логики из static/js/components/settingsParser.js (cloneBlock,
// isBlockEditable, getBlockDisplayText, updateBlockValue, getBlockClasses).

interface PaletteItem { type: string; label: string; title: string; disabled?: boolean; reason?: string }
const props = defineProps<{
  blocks: PatternBlock[]
  context: "if" | "then"
}>()

const BLOCK_PALETTE: PaletteItem[] = [
  { type: "text", label: "Текст", title: "Точный текст" },
  { type: "or", label: t("rules.block.or.label"), title: t("rules.block.or.title") },
  { type: "number", label: "Число", title: "Любое число (1, 2, 10, 155...)" },
  { type: "range", label: "Диапазон", title: "Диапазон номеров (например, 1, 3-10 или 1-180)" },
  { type: "add", label: "+", title: "Сложение" },
  { type: "subtract", label: "-", title: "Вычитание" },
  { type: "whitespace", label: "Пробел", title: "Один или несколько пробелов" },
  { type: "any_text", label: "*", title: "Любой текст (нежадный поиск)" },
  { type: "start_of_line", label: "Начало", title: "Соответствует началу названия" },
  { type: "end_of_line", label: "Конец", title: "Соответствует концу названия" },
]

// 'if' context enables range filtering; 'then' context enables mathematical operations.
const palette = computed<PaletteItem[]>(() => {
  const items = props.context === "if"
    ? BLOCK_PALETTE.filter((b) => !["add", "subtract"].includes(b.type))
    : BLOCK_PALETTE.filter((b) => b.type !== "range")
  return items.map((item) => {
    const reason = disabledReason(item.type)
    return reason ? { ...item, disabled: true, reason } : item
  })
})

// A template holds either several numbers or a single number with an attachment:
// the attachment belongs to that one number, so the two cannot be combined.
function disabledReason(type: string): string {
  const hasAttachment = attachmentBlockCount(props.blocks) > 0
  if (isAttachmentType(type)) {
    if (hasAttachment) return "В шаблоне уже есть блок-приложение"
    if (numberBlockCount(props.blocks) >= 2) return "В шаблоне уже два числа"
    return ""
  }
  if (type === "number" && hasAttachment) return "В шаблоне уже есть блок-приложение"
  return ""
}

function cloneBlock(original: PaletteItem): PatternBlock {
  const newBlock: PatternBlock = { id: Date.now() + Math.random(), type: original.type }
  if (original.type === "text") newBlock.value = ""
  if (original.type === "add" || original.type === "subtract") newBlock.value = "1"
  if (original.type === "range") newBlock.value = "1, 3-10"
  return newBlock
}

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

// Fool-proofing: the template that receives the block is taken from the drop
// target, not from this editor: the sortable fires the check on the source.
function checkMove(evt: any): boolean {
  const dragged = evt.draggedContext?.element as PatternBlock | undefined
  const source = evt.draggedContext?.list as PatternBlock[] | undefined
  const target = evt.relatedContext?.list as PatternBlock[] | undefined
  if (!dragged || !target) return true
  // A number and its attachment are one unit and are not split apart.
  if (source && source !== target && splitsAttachment(source, dragged)) return false
  const targetContext = evt.to?.getAttribute?.("data-context") ?? undefined
  return canDrop(target, dragged, targetContext)
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
      :move="checkMove"
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
            <span class="range-prefix">диапазон:</span>
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
              ? 'У числа прикреплён блок-приложение — удалите приложение'
              : 'Удалить блок'"
            @click="removeBlock(index)"
          />
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
        :filter="'.palette-btn.is-disabled'"
        :move="checkMove"
      >
        <template #item="{ element }">
          <div
            :class="['palette-btn', 'block-type-' + element.type, { 'is-disabled': element.disabled }]"
            :title="element.disabled ? element.reason : element.title"
          >{{ element.label }}</div>
        </template>
      </draggable>
    </div>
  </div>
</template>
