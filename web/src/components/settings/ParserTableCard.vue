<script setup lang="ts">
import { computed, ref } from "vue"
import draggable from "vuedraggable"
import ToggleSwitch from "primevue/toggleswitch"
import StSelect from "../StSelect.vue"
import { t } from "../../i18n"
import { normalizeAttachments, type PatternBlock } from "../../lib/patternBlocks"
import { emptyRow, fieldValues, rowOf, textsOf, type TableRow } from "../../lib/ruleTable"
import { actionLabel, actionOptions, fieldText, isAssign, isExtract, isNumberAssign } from "../../lib/ruleVocabulary"
import type { EditableRule } from "./ParserRuleCard.vue"
import ParserPatternEditor from "./ParserPatternEditor.vue"
import PatternPalette from "./PatternPalette.vue"

// A rule of the kind «Таблица»: rows of «ЕСЛИ» → «ТО» under one action chosen for the whole column. Each row is what an
// ordinary rule of one condition «содержит» and one action is; the core reads the rows in their order at the place of
// the rule. One palette serves the cell being edited; the header of «ЕСЛИ» adds a row for each value of a coded field of
// the lines of the test that the table does not hold yet.
type Table = NonNullable<EditableRule["table"]>
type Cell = { key: number; side: "if" | "then" }

const props = defineProps<{
  /** A rule whose `table` is set (the list shows this card only for such a rule). */
  rule: EditableRule
  index: number
  total: number
  isOpen: boolean
  /** The lines of the test of the profile (step 3): the values of the quick templates. */
  lines: string[]
  /** The codes of the fields of a line of the constructor, in its order (core LINE_FIELDS). */
  codes: string[]
}>()
const emit = defineEmits<{ toggle: []; save: []; delete: []; move: [dir: -1 | 1] }>()

const ACTIONS = actionOptions()
const selected = ref<Cell | null>(null)
// The rows the last quick template added, marked until the next one.
const fresh = ref<Set<number>>(new Set())

const table = computed(() => props.rule.table as Table)
const toggleIcon = computed(() => (props.isOpen ? "pi-chevron-down" : "pi-chevron-right"))
const extracts = computed(() => isExtract(table.value.action_type))
const assigns = computed(() => isAssign(table.value.action_type))

const templates = computed(() => {
  const taken = textsOf(table.value.rows)
  return props.codes.map((code) => ({
    code, missing: fieldValues(props.lines, code).filter((value) => !taken.has(`${code}:${value}`)),
  }))
})

// The template the palette serves: the cell being edited, when its side holds blocks.
const target = computed<{ blocks: PatternBlock[]; context: "if" | "then" } | null>(() => {
  const cell = selected.value
  const row = cell && table.value.rows.find((each) => each.key === cell.key)
  if (!row) return null
  if (cell.side === "if") return { blocks: row.ifBlocks, context: "if" }
  return extracts.value ? { blocks: row.thenBlocks, context: "then" } : null
})

const isSelected = (row: TableRow, side: Cell["side"]) => selected.value?.key === row.key && selected.value.side === side
function select(row: TableRow, side: Cell["side"]): void {
  selected.value = { key: row.key, side }
}
function pick(block: PatternBlock): void {
  if (!target.value) return
  target.value.blocks.push(block)
  normalizeAttachments(target.value.blocks)
}
function fill(code: string, values: string[]): void {
  const rows = values.map((value) => rowOf(code, value))
  table.value.rows.push(...rows)
  fresh.value = new Set(rows.map((row) => row.key))
}
function addRow(): void {
  const row = emptyRow()
  table.value.rows.push(row)
  select(row, "if")
}
function removeRow(at: number): void {
  const [gone] = table.value.rows.splice(at, 1)
  if (gone && selected.value?.key === gone.key) selected.value = null
}
function setAction(value: string): void {
  table.value.action_type = value
}
</script>

<template>
  <div class="rule-card rule-table-card">
    <div class="rule-header">
      <div class="rule-title" @click="emit('toggle')">
        <i class="pi rule-toggle-icon" :class="toggleIcon" />
        <i class="pi pi-table rule-kind" :title="t('rules.table.kind')" />
        <input v-model="rule.name" type="text" class="rule-name-input" :placeholder="t('rules.rule.namePlaceholder')" @click.stop />
        <span v-if="rule.valid === false" class="rule-invalid-badge" :title="rule.error || t('rules.rule.disabled.title')">
          {{ t("rules.rule.disabled.label") }}
        </span>
        <span class="rule-rows">{{ t("rules.table.rows", { count: table.rows.length }) }}</span>
      </div>
      <div class="rule-controls">
        <button class="control-btn" :disabled="index === 0" :title="t('rules.rule.up')" @click="emit('move', -1)">
          <i class="pi pi-arrow-up" />
        </button>
        <button class="control-btn" :disabled="index === total - 1" :title="t('rules.rule.down')" @click="emit('move', 1)">
          <i class="pi pi-arrow-down" />
        </button>
        <ToggleSwitch v-model="rule.continue_after_match" class="mx-2" :title="t('rules.rule.continue')" @change="emit('save')" />
        <button class="control-btn text-success" :title="t('rules.rule.save')" @click="emit('save')">
          <i class="pi pi-save" />
        </button>
        <button class="control-btn text-danger" :title="t('rules.rule.delete')" @click="emit('delete')">
          <i class="pi pi-trash" />
        </button>
      </div>
    </div>

    <div v-if="isOpen" class="rule-body table-body">
      <PatternPalette class="table-palette" :blocks="target?.blocks ?? null" :context="target?.context ?? 'if'" @pick="pick" />

      <div class="table-scroll">
      <table class="rule-table">
        <thead>
          <tr>
            <th class="col-handle" />
            <th class="col-if">
              <div class="cells">
                <span class="table-label label-if">{{ t("rules.label.if") }}</span>
                <button
                  v-for="each in templates" :key="each.code" type="button" class="template-chip"
                  :disabled="each.missing.length === 0"
                  :title="t('rules.table.template', { code: each.code, count: each.missing.length })"
                  @click="fill(each.code, each.missing)"
                >{{ each.code }}:<span class="template-count">{{ each.missing.length }}</span></button>
              </div>
            </th>
            <th class="col-then">
              <div class="cells">
                <span class="table-label label-then">{{ t("rules.label.then") }}</span>
                <StSelect class="table-action" :model-value="table.action_type" :options="ACTIONS"
                          @update:model-value="setAction($event as string)" />
              </div>
            </th>
            <th class="col-remove" />
          </tr>
        </thead>
        <draggable :list="table.rows" item-key="key" tag="tbody" handle=".row-handle" :animation="150">
          <template #item="{ element: row, index: at }">
            <tr :class="{ 'is-fresh': fresh.has(row.key) }">
              <td class="col-handle row-handle" :title="t('rules.table.drag')">
                <svg class="handle-dots" width="12" height="16" viewBox="0 0 12 16" fill="currentColor" aria-hidden="true">
                  <circle cx="4" cy="4" r="1.3" /><circle cx="8" cy="4" r="1.3" /><circle cx="4" cy="8" r="1.3" />
                  <circle cx="8" cy="8" r="1.3" /><circle cx="4" cy="12" r="1.3" /><circle cx="8" cy="12" r="1.3" />
                </svg>
              </td>
              <td class="col-if" :class="{ 'is-selected': isSelected(row, 'if') }" @mousedown="select(row, 'if')">
                <ParserPatternEditor :blocks="row.ifBlocks" context="if" :show-palette="false" />
              </td>
              <td class="col-then" :class="{ 'is-selected': extracts && isSelected(row, 'then') }" @mousedown="select(row, 'then')">
                <ParserPatternEditor v-if="extracts" :blocks="row.thenBlocks" context="then" :show-palette="false" />
                <input
                  v-else-if="assigns" :value="row.thenValue" class="table-value" :type="isNumberAssign(table.action_type) ? 'number' : 'text'"
                  @input="row.thenValue = fieldText($event)"
                  :placeholder="table.action_type === 'assign_range' ? t('rules.action.assign_range.hint') : t('rules.action.valuePlaceholder')"
                  :aria-label="actionLabel(table.action_type)"
                />
              </td>
              <td class="col-remove">
                <button type="button" class="row-remove" :title="t('rules.table.removeRow')" :aria-label="t('rules.table.removeRow')" @click="removeRow(at)">
                  <i class="pi pi-times" />
                </button>
              </td>
            </tr>
          </template>
          <template #footer>
            <tr>
              <td class="col-handle" />
              <td colspan="3">
                <button type="button" class="table-add" @click="addRow"><i class="pi pi-plus" /> {{ t("rules.table.addRow") }}</button>
              </td>
            </tr>
          </template>
        </draggable>
      </table>
      </div>
    </div>
  </div>
</template>

<style scoped>
.rule-kind { color: var(--text-muted); }
.rule-rows { flex: 0 0 auto; margin-left: auto; color: var(--text-muted); font-weight: 400; font-size: 0.875rem; white-space: nowrap; }
/* On a narrow card the controls go under the name instead of over it. */
.rule-table-card .rule-header { flex-wrap: wrap; }
.rule-table-card .rule-title { flex: 1 1 14rem; }
.rule-table-card .rule-controls { margin-left: auto; }
.table-body { display: flex; flex-direction: column; gap: 0.625rem; }

/* One grid with one frame: the columns line up through the whole table, a row is one line. */
/* The table keeps its columns at any width: on a card narrower than the table can be, it scrolls in its own box, and
   the window around it does not. */
.table-scroll { overflow-x: auto; }
.rule-table {
  width: 100%; min-width: 34rem; table-layout: fixed; border-collapse: separate; border-spacing: 0;
  border: 1px solid var(--border-color); border-radius: 8px; overflow: hidden;
}
.rule-table th, .rule-table td { padding: 0.5rem 0.625rem; text-align: left; vertical-align: middle; border-top: 1px solid #e9ecef; }
.rule-table thead th { background: #f8f9fa; border-top: 0; font-weight: 400; }
.rule-table .col-if, .rule-table .col-then { border-left: 1px solid #e9ecef; }
/* The narrow columns have no side padding: their width is all theirs, and their mark stands in its middle. */
.rule-table th.col-handle, .rule-table th.col-remove { width: 2.25rem; }
.rule-table .col-handle, .rule-table .col-remove { padding-left: 0; padding-right: 0; }
.rule-table th.col-then { width: 34%; }
.col-handle, .col-remove { text-align: center !important; color: #adb5bd; }
.row-handle { cursor: grab; }
/* The dots are drawn in the middle of their own box, and the box stands in the middle of the cell. */
.handle-dots { display: block; margin: 0 auto; }
.cells { display: flex; flex-wrap: wrap; gap: 0.375rem; align-items: center; }

.table-label { display: inline-block; padding: 0.25rem 0.75rem; border-radius: 4px; font-weight: 700; font-size: 0.875rem; }
.table-label.label-if { color: #0d6efd; background: #cfe2ff; }
.table-label.label-then { color: #198754; background: #d1e7dd; }
.template-chip {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 0.8rem; padding: 0.2rem 0.45rem;
  border: 1px solid #ced4da; border-radius: 4px; background: #fff; color: inherit; cursor: pointer;
}
.template-chip:hover:not(:disabled) { border-color: #0d6efd; color: #0d6efd; }
.template-chip:disabled { color: #adb5bd; border-color: #e9ecef; cursor: default; }
.template-count { margin-left: 0.25rem; color: #6c757d; }
.template-chip:disabled .template-count { color: #ced4da; }
.table-action { flex: 1 1 10rem; min-width: 0; border: 1px solid #ced4da; border-radius: 4px; background: #fff; }

.rule-table tr.is-fresh td { background: #f1f7ff; }
.rule-table td.is-selected { box-shadow: inset 0 0 0 2px #0d6efd; }
/* The template of a cell is the plain row of its blocks: the frame is the cell's. */
.rule-table td :deep(.pattern-constructor) { padding: 0; background: transparent; }
.rule-table td :deep(.pattern-blocks-container) { min-height: 2rem; padding: 0; }
.table-value {
  display: block; width: 100%; box-sizing: border-box; padding: 0.35rem 0.625rem; font: inherit; font-size: 0.875rem;
  border: 1px solid #ced4da; border-radius: 4px; background: #fff;
}
.row-remove { display: block; margin: 0 auto; border: 0; background: none; padding: 0.25rem; color: #adb5bd; cursor: pointer; }
.row-remove:hover { color: var(--color-red-1); }
.table-add { display: inline-flex; align-items: center; gap: 0.375rem; padding: 0.125rem 0; border: 0; background: none; color: #0d6efd; font: inherit; font-size: 0.875rem; cursor: pointer; }
</style>
