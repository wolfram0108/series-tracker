<script setup lang="ts">
import { computed, ref } from "vue"
import draggable from "vuedraggable"
import ToggleSwitch from "primevue/toggleswitch"
import StSelect from "../StSelect.vue"
import { t } from "../../i18n"
import { normalizeAttachments, type PatternBlock } from "../../lib/patternBlocks"
import {
  canJoin, canJoinRange, cellsIn, emptyRow, fieldValues, join, joinRange, joined, rangeOf, rowOf, settle, spanOf,
  splitRange, textsOf, type Side, type TableRow,
} from "../../lib/ruleTable"
import { actionLabel, actionOptions, choicesOf, fieldText, folderLanguages, folderValueOf, folderValueText, isAssign, isExtract,
  isFolderAction, isNumberAssign, type RulePlace } from "../../lib/ruleVocabulary"
import type { EditableRule } from "./ParserRuleCard.vue"
import SubtitleTypes from "../SubtitleTypes.vue"
import NumberMark from "../NumberMark.vue"
import ParserPatternEditor from "./ParserPatternEditor.vue"
import PatternPalette from "./PatternPalette.vue"
import { reasonText } from "../../lib/reason"

// A rule of the kind «Таблица»: rows of «ЕСЛИ» → «ТО» under one action chosen for the whole column. Each row is what an
// ordinary rule of one condition «содержит» and one action is; the core reads the rows in their order at the place of
// the rule. One palette serves the cell being edited; the header of «ЕСЛИ» adds a row for each value of a coded field of
// the lines of the test that the table does not hold yet.
type Table = NonNullable<EditableRule["table"]>
type Cell = { key: number; side: Side }

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
  /** Where the card stands: its «ТО» offers only the actions of that place (lib/ruleVocabulary.ts ACTION_PLACES). */
  place?: RulePlace
}>()
const emit = defineEmits<{ toggle: []; save: []; delete: []; move: [dir: -1 | 1] }>()

// The actions of «ТО», numbered: a row shows its action by its number (compact), the list names them with it.
const ACTIONS = actionOptions(props.place).map((each, at) => ({ ...each, label: `${at + 1}. ${each.label}` }))
const numberOf = (type: string): number => ACTIONS.findIndex((each) => each.value === type) + 1
// The cell clicked, and the row its choice was extended to (Shift+click): the cells of the column between the two are
// chosen together.
const selected = ref<Cell | null>(null)
const upto = ref<number | null>(null)
// The rows the last quick template added, marked until the next one.
const fresh = ref<Set<number>>(new Set())

const table = computed(() => props.rule.table as Table)
const toggleIcon = computed(() => (props.isOpen ? "pi-chevron-down" : "pi-chevron-right"))
// The action of a row: its own, else the column's.
const actionOf = (row: TableRow): string => row.then.action || table.value.action_type
// A table of folders: any row whose action names a folder.
const folders = computed(() => table.value.rows.some((row) => isFolderAction(actionOf(row))) || isFolderAction(table.value.action_type))

const templates = computed(() => {
  const taken = textsOf(table.value.rows)
  return props.codes.map((code) => ({
    code, missing: fieldValues(props.lines, code).filter((value) => !taken.has(`${code}:${value}`)),
  }))
})

// The rows the chosen cells span, and how many cells they are.
const picked = computed<{ side: Side; first: number; end: number; cells: number } | null>(() => {
  const cell = selected.value
  const rows = table.value.rows
  const from = cell ? rows.findIndex((each) => each.key === cell.key) : -1
  if (!cell || from < 0) return null
  const to = upto.value === null ? -1 : rows.findIndex((each) => each.key === upto.value)
  const [first, end] = rangeOf(rows, from, to < 0 ? from : to, cell.side)
  return { side: cell.side, first, end, cells: cellsIn(rows, first, end, cell.side) }
})

// The template the palette serves: the one cell chosen, when its side holds blocks.
const target = computed<{ blocks: PatternBlock[]; context: "if" | "then" } | null>(() => {
  const chosen = picked.value
  if (!chosen || chosen.cells !== 1) return null
  const row = table.value.rows[chosen.first]
  if (chosen.side === "if") return { blocks: row.if.blocks, context: "if" }
  return isExtract(actionOf(row)) ? { blocks: row.then.blocks, context: "then" } : null
})

/** How the cell that begins at the row stands in the choice: the one cell chosen, or its place in a run of several. */
function markOf(at: number, side: Side): Record<string, boolean> {
  const chosen = picked.value
  const [first, end] = spanOf(table.value.rows, at, side)
  if (!chosen || chosen.side !== side || first < chosen.first || end > chosen.end) return {}
  if (chosen.cells === 1) return { "is-selected": true }
  return { "is-picked": true, "is-first": first === chosen.first, "is-last": end === chosen.end }
}
function select(row: TableRow, side: Side, event?: MouseEvent): void {
  if (event?.shiftKey && selected.value?.side === side) {
    // The choice is extended, nothing is typed: the click neither moves the caret nor selects text.
    event.preventDefault()
    upto.value = row.key
    return
  }
  selected.value = { key: row.key, side }
  upto.value = null
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
  if (gone && (selected.value?.key === gone.key || upto.value === gone.key)) {
    selected.value = null
    upto.value = null
  }
  settle(table.value.rows)
}
// Joined cells: a cell spans the rows that hold it. Several chosen cells are made one; one chosen cell takes the cell
// below it. The joined cell holds what the uppermost held.
const rowsOfCell = (at: number, side: Side): number => {
  const [first, end] = spanOf(table.value.rows, at, side)
  return end - first
}
const isJoined = (at: number, side: Side): boolean => joined(table.value.rows, at, side)
const mayJoin = computed(() => {
  const chosen = picked.value
  if (!chosen) return false
  return chosen.cells > 1 ? canJoinRange(table.value.rows, chosen.first, chosen.end, chosen.side)
    : canJoin(table.value.rows, chosen.first, chosen.side)
})
const maySplit = computed(() => {
  const chosen = picked.value
  return chosen !== null && chosen.end - chosen.first > chosen.cells
})
function joinPicked(): void {
  const chosen = picked.value
  if (!chosen || !mayJoin.value) return
  const rows = table.value.rows
  if (chosen.cells > 1) joinRange(rows, chosen.first, chosen.end, chosen.side)
  else join(rows, chosen.first, chosen.side)
  selected.value = { key: rows[chosen.first].key, side: chosen.side }
  upto.value = null
}
function splitPicked(): void {
  const chosen = picked.value
  if (!chosen) return
  splitRange(table.value.rows, chosen.first, chosen.end, chosen.side)
  upto.value = table.value.rows[chosen.end - 1].key
}
function moved(): void {
  settle(table.value.rows)
  selected.value = null
  upto.value = null
}
function setAction(value: string): void {
  table.value.action_type = value
  // A row whose own action is now the column's has none of its own.
  for (const row of table.value.rows) if (row.then.action === value) delete row.then.action
}
function setRowAction(row: TableRow, value: string): void {
  if (value === table.value.action_type) delete row.then.action
  else row.then.action = value
}
</script>

<template>
  <div class="rule-card rule-table-card">
    <div class="rule-header">
      <div class="rule-title" @click="emit('toggle')">
        <button type="button" class="rule-toggle" :aria-expanded="isOpen" :aria-label="t('rules.rule.toggle')" @click.stop="emit('toggle')">
          <i class="pi rule-toggle-icon" :class="toggleIcon" aria-hidden="true" />
        </button>
        <i class="pi pi-table rule-kind" :title="t('rules.table.kind')" />
        <!-- The number of the rule in its profile: the mark «Откуда» shows for what it placed. -->
        <NumberMark :n="index + 1" :title="t('rules.rule.number', { n: index + 1 })" />
        <input v-model="rule.name" type="text" class="rule-name-input" :placeholder="t('rules.rule.namePlaceholder')" @click.stop />
        <span v-if="rule.valid === false" class="rule-invalid-badge" :title="reasonText(rule.error) || t('rules.rule.disabled.title')">
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
      <div class="table-bar">
        <PatternPalette
          class="table-palette" :class="{ 'is-idle': picked !== null && !target }"
          :blocks="target?.blocks ?? null" :context="target?.context ?? 'if'" @pick="pick"
        />
        <div class="cell-tools">
          <span class="cell-tools-name">{{ t("rules.table.cells") }}</span>
          <button type="button" class="cell-tool cell-join" :disabled="!mayJoin" @click="joinPicked">
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true">
              <rect x="2.5" y="1.5" width="11" height="13" rx="1" /><path d="M8 3.5v3M6.5 5.2L8 6.7l1.5-1.5M8 12.5v-3M6.5 10.8L8 9.3l1.5 1.5" />
            </svg>{{ t("rules.table.join") }}
          </button>
          <button type="button" class="cell-tool cell-split" :disabled="!maySplit" @click="splitPicked">
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true">
              <rect x="2.5" y="1.5" width="11" height="13" rx="1" /><path d="M2.5 8h11M8 6.3v-3M6.5 4.8L8 3.3l1.5 1.5M8 9.7v3M6.5 11.2L8 12.7l1.5-1.5" />
            </svg>{{ t("rules.table.split") }}
          </button>
        </div>
      </div>

      <div class="table-scroll">
      <table class="rule-table" :class="{ 'is-folders': folders }">
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
                <NumberMark :n="numberOf(table.action_type)" :title="actionLabel(table.action_type)" />
                <StSelect class="table-action" :model-value="table.action_type" :options="ACTIONS" :display="actionLabel(table.action_type)"
                          @update:model-value="setAction($event as string)" />
                <!-- The language of every row with none of its own. -->
                <StSelect
                  v-if="folders" class="table-language table-default-language"
                  :model-value="table.language || null" :options="folderLanguages()" :placeholder="t('rules.action.language')"
                  :aria-label="t('rules.action.defaultLanguage')" @update:model-value="table.language = String($event ?? '')"
                />
              </div>
            </th>
            <th class="col-remove" />
          </tr>
        </thead>
        <draggable :list="table.rows" item-key="key" tag="tbody" handle=".row-handle" :animation="150" @end="moved">
          <template #item="{ element: row, index: at }">
            <tr :class="{ 'is-fresh': fresh.has(row.key) }">
              <td class="col-handle row-handle" :title="t('rules.table.drag')">
                <svg class="handle-dots" width="12" height="16" viewBox="0 0 12 16" fill="currentColor" aria-hidden="true">
                  <circle cx="4" cy="4" r="1.3" /><circle cx="8" cy="4" r="1.3" /><circle cx="4" cy="8" r="1.3" />
                  <circle cx="8" cy="8" r="1.3" /><circle cx="4" cy="12" r="1.3" /><circle cx="8" cy="12" r="1.3" />
                </svg>
              </td>
              <td
                v-if="!isJoined(at, 'if')" class="col-if" :rowspan="rowsOfCell(at, 'if')"
                :class="[markOf(at, 'if'), { 'is-joined': rowsOfCell(at, 'if') > 1 }]" @mousedown="select(row, 'if', $event)"
              >
                <ParserPatternEditor :blocks="row.if.blocks" context="if" :show-palette="false" />
              </td>
              <td
                v-if="!isJoined(at, 'then')" class="col-then" :rowspan="rowsOfCell(at, 'then')"
                :class="[markOf(at, 'then'), { 'is-joined': rowsOfCell(at, 'then') > 1 }]" @mousedown="select(row, 'then', $event)"
              >
                <div class="row-then">
                  <!-- The action of the row by its number in the list of «ТО»; dimmed while it is the column's. -->
                  <StSelect
                    class="row-action" :class="{ 'is-own': !!row.then.action }" :model-value="actionOf(row)" :options="ACTIONS"
                    :style="{ '--action': `var(--source-${(numberOf(actionOf(row)) - 1) % 16})` }"
                    :display="String(numberOf(actionOf(row)))" :aria-label="t('rules.table.rowAction')" :list-min-width="16"
                    @update:model-value="setRowAction(row, $event as string)"
                  />
                  <div class="row-then-value">
                    <ParserPatternEditor v-if="isExtract(actionOf(row))" :blocks="row.then.blocks" context="then" :show-palette="false" />
                    <!-- A folder with its language: two fields of one value. -->
                    <div v-else-if="isFolderAction(actionOf(row))" class="table-folder">
                      <input
                        :value="folderValueOf(row.then.value).name" class="table-value" type="text" :placeholder="t('rules.action.folderName')"
                        :aria-label="t('rules.action.folderName')"
                        @input="row.then.value = folderValueText({ ...folderValueOf(row.then.value), name: fieldText($event) })"
                      />
                      <StSelect
                        class="table-language" :model-value="folderValueOf(row.then.value).language || null" :options="folderLanguages()"
                        :placeholder="table.language || t('rules.action.language')"
                        :class="{ 'is-default': !folderValueOf(row.then.value).language && table.language }"
                        @update:model-value="row.then.value = folderValueText({ ...folderValueOf(row.then.value), language: String($event ?? '') })"
                      />
                      <SubtitleTypes
                        v-if="actionOf(row) === 'assign_subtitle_folder'" class="table-type"
                        :model-value="folderValueOf(row.then.value).type ?? ''" :label="t('rules.action.subtitleType')"
                        @update:model-value="row.then.value = folderValueText({ ...folderValueOf(row.then.value), type: $event })"
                      />
                    </div>
                    <StSelect
                      v-else-if="isAssign(actionOf(row)) && choicesOf(actionOf(row))" class="table-value" :model-value="row.then.value || null"
                      :options="choicesOf(actionOf(row))!" :placeholder="t('rules.action.valuePlaceholder')"
                      @update:model-value="row.then.value = String($event ?? '')"
                    />
                    <input
                      v-else-if="isAssign(actionOf(row))" :value="row.then.value" class="table-value"
                      :type="isNumberAssign(actionOf(row)) ? 'number' : 'text'"
                      :placeholder="actionOf(row) === 'assign_range' ? t('rules.action.assign_range.hint') : t('rules.action.valuePlaceholder')"
                      :aria-label="actionLabel(actionOf(row))"
                      @input="row.then.value = fieldText($event)"
                    />
                  </div>
                </div>
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
.table-body { display: flex; flex-direction: column; gap: 0.375rem; }

/* One grid with one frame: the columns line up through the whole table, a row is one line. */
/* The table keeps its columns at any width: on a card narrower than the table can be, it scrolls in its own box, and
   the window around it does not. */
.table-scroll { overflow-x: auto; }
.rule-table {
  width: 100%; min-width: 34rem; table-layout: fixed; border-collapse: separate; border-spacing: 0;
  border: 1px solid var(--border-color); border-radius: 8px; overflow: hidden;
}
.rule-table th, .rule-table td { padding: 0.3125rem 0.5rem; text-align: left; vertical-align: middle; border-top: 1px solid #e9ecef; }
.rule-table thead th { background: #f8f9fa; border-top: 0; font-weight: 400; }
.rule-table .col-if, .rule-table .col-then { border-left: 1px solid #e9ecef; }
/* The narrow columns have no side padding: their width is all theirs, and their mark stands in its middle. */
.rule-table th.col-handle, .rule-table th.col-remove { width: 2.25rem; }
.rule-table .col-handle, .rule-table .col-remove { padding-left: 0; padding-right: 0; }
.rule-table th.col-then { width: 34%; }
/* A table of folders holds two fields in «ТО» (the name and the language) and a language in its head: the column is wider. */
.rule-table.is-folders th.col-then { width: 44%; }
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
.template-count { margin-left: 0.25rem; color: var(--text-muted); }
.template-chip:disabled .template-count { color: #ced4da; }
.table-action { flex: 1 1 10rem; min-width: 0; border: 1px solid #ced4da; border-radius: 4px; background: #fff; }
/* The action and the language of the head are one line each and one height. */
.table-action, .table-default-language { min-height: 2rem; }
.table-action :deep(.selected-value) { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

.rule-table tr.is-fresh td { background: #f1f7ff; }
.rule-table td.is-selected { box-shadow: inset 0 0 0 2px #0d6efd; }
/* Several cells of a column chosen together: one frame around all of them. */
.rule-table td.is-picked { background: #eaf2ff; box-shadow: inset 2px 0 0 #0d6efd, inset -2px 0 0 #0d6efd; }
.rule-table td.is-picked.is-first { box-shadow: inset 2px 2px 0 #0d6efd, inset -2px 0 0 #0d6efd; }
.rule-table td.is-picked.is-last { box-shadow: inset 2px -2px 0 #0d6efd, inset -2px 0 0 #0d6efd; }
/* A joined cell is one cell of several rows: what it holds stands in its middle. */
.rule-table td.is-joined { background: #fbfcfe; }
/* The palette of blocks and the tools of the cells stand in one line over the table, and wrap when it is narrow. */
.table-bar { display: flex; flex-wrap: wrap; gap: 0.375rem 0.625rem; align-items: center; justify-content: space-between; }
.table-palette.is-idle { opacity: 0.45; }
.cell-tools { display: flex; flex-wrap: wrap; gap: 0.375rem; align-items: center; }
.cell-tools-name { margin-right: 0.125rem; color: var(--text-muted); font-size: 0.8125rem; }
.cell-tool {
  display: inline-flex; align-items: center; gap: 0.375rem; block-size: 1.625rem; padding: 0 0.5rem; border: 1px solid #9ec5fe;
  border-radius: 6px; background: #fff; color: #0a58ca; font: inherit; font-size: 0.8125rem; cursor: pointer;
}
.cell-tool:hover:not(:disabled) { border-color: #0d6efd; }
.cell-tool:disabled { border-color: #e9ecef; color: #adb5bd; cursor: default; }
/* The template of a cell is the plain row of its blocks: the frame is the cell's. */
.rule-table td :deep(.pattern-constructor) { padding: 0; background: transparent; }
.rule-table td :deep(.pattern-blocks-container) { min-height: 1.5rem; padding: 0; }
/* «ТО» of a row: the number of its action, then its value. */
.row-then { display: flex; gap: 0.375rem; align-items: center; }
.row-then-value { flex: 1 1 auto; min-width: 0; }
/* The number of the action of a row in its colour (the colours of the numbered lists, NumberMark): dimmed while it is the
   column's, whole when it is the row's own. */
.row-action {
  flex: 0 0 auto; min-height: 2rem; padding: 0 0.375rem; border: 0; border-radius: 4px; background: var(--action);
  font-size: 0.875rem; font-weight: 700; color: #fff; opacity: 0.45;
}
.row-action.is-own { opacity: 1; }
.row-action :deep(.selected-value), .row-action :deep(.chevron) { white-space: nowrap; padding: 0; color: #fff; }
.table-folder { display: flex; gap: 0.375rem; align-items: stretch; }
.table-folder > .table-type { flex: none; align-self: center; }
.table-folder > input.table-value { flex: 1 1 8rem; min-width: 0; }
.table-folder > .table-language {
  flex: 0 0 5rem; min-width: 0; padding: 0 0.5rem; border: 1px solid #ced4da; border-radius: 4px; background: #fff; font-size: 0.875rem;
}
.table-default-language { flex: 0 0 5rem; padding: 0 0.5rem; border: 1px solid #ced4da; border-radius: 4px; background: #fff; font-size: 0.875rem; }
.table-default-language :deep(.selected-value) { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; padding: 0; }
/* A row with no language of its own shows the table's, dimmed: it is the default, not the row's. */
.table-folder > .table-language.is-default :deep(.selected-value) { color: var(--text-muted); }
/* The language is one line: a long name is cut, the chevron keeps its place. */
.table-folder > .table-language :deep(.selected-value) { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; padding: 0; }
.table-value {
  display: block; width: 100%; box-sizing: border-box; padding: 0.35rem 0.625rem; font: inherit; font-size: 0.875rem;
  border: 1px solid #ced4da; border-radius: 4px; background: #fff;
}
.row-remove { display: block; margin: 0 auto; border: 0; background: none; padding: 0.25rem; color: #adb5bd; cursor: pointer; }
.row-remove:hover { color: var(--color-red-1); }
.table-add { display: inline-flex; align-items: center; gap: 0.375rem; padding: 0.125rem 0; border: 0; background: none; color: #0d6efd; font: inherit; font-size: 0.875rem; cursor: pointer; }
</style>
