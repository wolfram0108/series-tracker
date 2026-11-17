<script setup lang="ts">
import { computed } from "vue"
import ToggleSwitch from "primevue/toggleswitch"
import StGroup from "../StGroup.vue"
import StSelect from "../StSelect.vue"
import { t } from "../../i18n"
import ParserPatternEditor, { type PatternBlock } from "./ParserPatternEditor.vue"
import type { TableRow } from "../../lib/ruleTable"
import { actionLabel, actionOptions, choicesOf, conditionOptions, fieldText, folderLanguages, folderValueOf, folderValueText, isAssign,
  isExtract, isFolderAction, isNumberAssign, type RulePlace } from "../../lib/ruleVocabulary"
import { reasonText } from "../../lib/reason"

// Карточка одного правила профиля парсера. Шапка (имя + управление) и
// тело (условия «ЕСЛИ» с конструктором паттернов + действия «ТО»).
// Порт static/js/components/settingsParser.js (методы add/remove
// Condition/Action, onActionTypeChange, getAssignActionLabel).

interface Condition {
  id?: number
  condition_type: string
  logical_operator: string
  _blocks: PatternBlock[]
}
interface Action {
  action_type: string
  action_pattern: string
  _action_blocks: PatternBlock[]
}
export interface EditableRule {
  id: number | string
  name: string
  continue_after_match: boolean
  is_new?: boolean
  valid?: boolean
  error?: string | null
  conditions: Condition[]
  actions: Action[]
  /** A rule «Таблица»: the action of its column and its rows (ParserTableCard); none for an ordinary rule. */
  table?: { action_type: string; rows: TableRow[] } | null
}

const props = defineProps<{
  rule: EditableRule
  index: number
  total: number
  isOpen: boolean
  /** Where the card stands: its «ТО» offers only the actions of that place (lib/ruleVocabulary.ts ACTION_PLACES). */
  place?: RulePlace
}>()

const emit = defineEmits<{
  toggle: []
  save: []
  delete: []
  move: [dir: -1 | 1]
}>()

const CONDITION_TYPES = conditionOptions()
const ACTION_TYPES = actionOptions(props.place)

const toggleIcon = computed(() => (props.isOpen ? "pi-chevron-down" : "pi-chevron-right"))

function addCondition(c_index: number): void {
  props.rule.conditions.splice(c_index + 1, 0, {
    condition_type: "contains",
    _blocks: [],
    logical_operator: "AND",
  })
}
function removeCondition(c_index: number): void {
  props.rule.conditions.splice(c_index, 1)
}
function addAction(): void {
  props.rule.actions.push({ action_type: "exclude", action_pattern: "[]", _action_blocks: [] })
}
function removeAction(a_index: number): void {
  props.rule.actions.splice(a_index, 1)
}
function onActionTypeChange(action: Action, value: string): void {
  action.action_type = value
  if (isExtract(value)) {
    action.action_pattern = "[]"
    action._action_blocks = []
  } else if (isNumberAssign(value)) {
    action.action_pattern = "1"
  } else {
    action._action_blocks = []
    action.action_pattern = ""
  }
}
function getAssignActionLabel(actionType: string): string {
  return isAssign(actionType) ? actionLabel(actionType) : t("rules.action.value")
}
</script>

<template>
  <div class="rule-card">
    <div class="rule-header">
      <div class="rule-title" @click="emit('toggle')">
        <button type="button" class="rule-toggle" :aria-expanded="isOpen" :aria-label="t('rules.rule.toggle')" @click.stop="emit('toggle')">
          <i class="pi rule-toggle-icon" :class="toggleIcon" aria-hidden="true" />
        </button>
        <input v-model="rule.name" type="text" class="rule-name-input" :placeholder="t('rules.rule.namePlaceholder')" @click.stop />
        <span
          v-if="rule.valid === false"
          class="rule-invalid-badge"
          :title="reasonText(rule.error) || t('rules.rule.disabled.title')"
        >{{ t("rules.rule.disabled.label") }}</span>
      </div>
      <div class="rule-controls">
        <button class="control-btn" :disabled="index === 0" :title="t('rules.rule.up')" @click="emit('move', -1)">
          <i class="pi pi-arrow-up" />
        </button>
        <button class="control-btn" :disabled="index === total - 1" :title="t('rules.rule.down')" @click="emit('move', 1)">
          <i class="pi pi-arrow-down" />
        </button>
        <ToggleSwitch
          v-model="rule.continue_after_match"
          class="mx-2"
          :title="t('rules.rule.continue')"
          @change="emit('save')"
        />
        <button class="control-btn text-success" :title="t('rules.rule.save')" @click="emit('save')">
          <i class="pi pi-save" />
        </button>
        <button class="control-btn text-danger" :title="t('rules.rule.delete')" @click="emit('delete')">
          <i class="pi pi-trash" />
        </button>
      </div>
    </div>

    <div v-if="isOpen" class="rule-body">
      <!-- ЕСЛИ -->
      <div class="rule-block if-block">
        <template v-for="(cond, c_index) in rule.conditions" :key="c_index">
          <div class="condition-group">
            <StGroup>
              <div class="constructor-item item-label label-if">{{ t("rules.label.if") }}</div>
              <StSelect
                :model-value="cond.condition_type"
                :options="CONDITION_TYPES"
                @update:model-value="cond.condition_type = $event as string"
              />
              <div class="constructor-item item-button-group">
                <button
                  class="btn-icon btn-delete"
                  :title="t('rules.condition.remove')"
                  :disabled="rule.conditions.length <= 1"
                  @click="removeCondition(c_index)"
                >
                  <i class="pi pi-times" />
                </button>
              </div>
            </StGroup>

            <StGroup class="group-auto-height mt-2">
              <div class="constructor-item item-pattern-editor">
                <ParserPatternEditor :blocks="cond._blocks" context="if" />
              </div>
              <div class="constructor-item item-button-group">
                <button class="btn-icon btn-add" :title="t('rules.condition.add')" @click="addCondition(c_index)">
                  <i class="pi pi-plus" />
                </button>
              </div>
            </StGroup>
          </div>

          <div v-if="c_index < rule.conditions.length - 1" class="logical-operator-container">
            <div class="circuit-toggle">
              <div class="circuit-body">
                <div class="circuit-line" />
                <div class="circuit-switch">
                  <button :class="{ active: cond.logical_operator === 'AND' }" @click="cond.logical_operator = 'AND'">{{ t("rules.operator.and") }}</button>
                  <button :class="{ active: cond.logical_operator === 'OR' }" @click="cond.logical_operator = 'OR'">{{ t("rules.operator.or") }}</button>
                </div>
                <div class="circuit-line" />
              </div>
            </div>
          </div>
        </template>
      </div>

      <!-- ТО -->
      <div class="rule-block then-block">
        <div v-for="(action, a_index) in rule.actions" :key="a_index" class="condition-group">
          <StGroup>
            <div class="constructor-item item-label label-then">{{ t("rules.label.then") }}</div>
            <StSelect
              :model-value="action.action_type"
              :options="ACTION_TYPES"
              @update:model-value="onActionTypeChange(action, $event as string)"
            />
            <div class="constructor-item item-button-group">
              <button
                class="btn-icon btn-delete"
                :title="t('rules.action.remove')"
                :disabled="rule.actions.length <= 1"
                @click="removeAction(a_index)"
              >
                <i class="pi pi-times" />
              </button>
            </div>
          </StGroup>

          <div v-if="action.action_type !== 'exclude'" class="action-content mt-2">
            <!-- extract_*: конструктор паттерна -->
            <StGroup v-if="isExtract(action.action_type)" class="group-auto-height">
              <div class="constructor-item item-pattern-editor">
                <ParserPatternEditor :blocks="action._action_blocks" context="then" />
              </div>
              <div v-if="a_index === rule.actions.length - 1" class="constructor-item item-button-group">
                <button class="btn-icon btn-add" :title="t('rules.action.add')" @click="addAction">
                  <i class="pi pi-plus" />
                </button>
              </div>
            </StGroup>

            <!-- assign_*: простое значение -->
            <StGroup v-else-if="isAssign(action.action_type)">
              <div class="constructor-item item-label">{{ getAssignActionLabel(action.action_type) }}</div>
              <!-- A folder is named with its language in one «ТО»: two fields, one value. -->
              <template v-if="isFolderAction(action.action_type)">
                <input
                  :value="folderValueOf(action.action_pattern).name" type="text" class="constructor-item item-input"
                  :placeholder="t('rules.action.folderName')" :aria-label="t('rules.action.folderName')"
                  @input="action.action_pattern = folderValueText({ ...folderValueOf(action.action_pattern), name: fieldText($event) })"
                />
                <StSelect
                  class="folder-language" :model-value="folderValueOf(action.action_pattern).language || null" :options="folderLanguages()"
                  :placeholder="t('rules.action.language')"
                  @update:model-value="action.action_pattern = folderValueText({ ...folderValueOf(action.action_pattern), language: String($event ?? '') })"
                />
              </template>
              <StSelect
                v-else-if="choicesOf(action.action_type)" :model-value="action.action_pattern || null" :options="choicesOf(action.action_type)!"
                :placeholder="t('rules.action.valuePlaceholder')" @update:model-value="action.action_pattern = String($event ?? '')"
              />
              <input
                v-else
                :value="action.action_pattern"
                :type="isNumberAssign(action.action_type) ? 'number' : 'text'"
                @input="action.action_pattern = fieldText($event)"
                class="constructor-item item-input"
                :placeholder="action.action_type === 'assign_range' ? t('rules.action.assign_range.hint') : t('rules.action.valuePlaceholder')"
              />
              <div v-if="a_index === rule.actions.length - 1" class="constructor-item item-button-group">
                <button class="btn-icon btn-add" :title="t('rules.action.add')" @click="addAction">
                  <i class="pi pi-plus" />
                </button>
              </div>
            </StGroup>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
