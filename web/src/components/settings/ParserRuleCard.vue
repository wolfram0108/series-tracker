<script setup lang="ts">
import { computed } from "vue"
import ToggleSwitch from "primevue/toggleswitch"
import StGroup from "../StGroup.vue"
import StSelect from "../StSelect.vue"
import ParserPatternEditor, { type PatternBlock } from "./ParserPatternEditor.vue"

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
  conditions: Condition[]
  actions: Action[]
}

const props = defineProps<{
  rule: EditableRule
  index: number
  total: number
  isOpen: boolean
}>()

const emit = defineEmits<{
  toggle: []
  save: []
  delete: []
  move: [dir: -1 | 1]
}>()

const CONDITION_TYPES = [
  { label: "Содержит", value: "contains" },
  { label: "Не содержит", value: "not_contains" },
]
const ACTION_TYPES = [
  { value: "exclude", label: "Исключить видео" },
  { value: "extract_single", label: "Извлечь номер серии" },
  { value: "extract_range", label: "Извлечь диапазон серий" },
  { value: "extract_season", label: "Установить номер сезона" },
  { value: "assign_voiceover", label: "Назначить озвучку/тег" },
  { value: "assign_episode", label: "Назначить номер серии" },
  { value: "assign_season", label: "Назначить номер сезона" },
  { value: "assign_quality", label: "Назначить качество" },
  { value: "assign_resolution", label: "Назначить разрешение" },
]
const EXTRACT_TYPES = ["extract_single", "extract_range", "extract_season"]
const ASSIGN_TYPES = ["assign_voiceover", "assign_episode", "assign_season", "assign_quality", "assign_resolution"]

const ASSIGN_LABELS: Record<string, string> = {
  assign_voiceover: "Назначить озвучку/тег",
  assign_episode: "Назначить номер серии",
  assign_season: "Назначить номер сезона",
  assign_quality: "Назначить качество",
  assign_resolution: "Назначить разрешение",
}

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
  if (EXTRACT_TYPES.includes(value)) {
    action.action_pattern = "[]"
    action._action_blocks = []
  } else if (["assign_episode", "assign_season"].includes(value)) {
    action.action_pattern = "1"
  } else {
    action._action_blocks = []
    action.action_pattern = ""
  }
}
function getAssignActionLabel(actionType: string): string {
  return ASSIGN_LABELS[actionType] || "Значение"
}
</script>

<template>
  <div class="rule-card">
    <div class="rule-header">
      <div class="rule-title" @click="emit('toggle')">
        <i class="pi rule-toggle-icon" :class="toggleIcon" />
        <input v-model="rule.name" type="text" class="rule-name-input" placeholder="Имя правила..." @click.stop />
      </div>
      <div class="rule-controls">
        <button class="control-btn" :disabled="index === 0" title="Поднять приоритет" @click="emit('move', -1)">
          <i class="pi pi-arrow-up" />
        </button>
        <button class="control-btn" :disabled="index === total - 1" title="Понизить приоритет" @click="emit('move', 1)">
          <i class="pi pi-arrow-down" />
        </button>
        <ToggleSwitch
          v-model="rule.continue_after_match"
          class="mx-2"
          title="Разрешить обработку следующими правилами после этого"
          @change="emit('save')"
        />
        <button class="control-btn text-success" title="Сохранить правило" @click="emit('save')">
          <i class="pi pi-save" />
        </button>
        <button class="control-btn text-danger" title="Удалить правило" @click="emit('delete')">
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
              <div class="constructor-item item-label label-if">ЕСЛИ</div>
              <StSelect
                :model-value="cond.condition_type"
                :options="CONDITION_TYPES"
                @update:model-value="cond.condition_type = $event as string"
              />
              <div class="constructor-item item-button-group">
                <button
                  class="btn-icon btn-delete"
                  title="Удалить условие"
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
                <button class="btn-icon btn-add" title="Добавить условие" @click="addCondition(c_index)">
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
                  <button :class="{ active: cond.logical_operator === 'AND' }" @click="cond.logical_operator = 'AND'">И</button>
                  <button :class="{ active: cond.logical_operator === 'OR' }" @click="cond.logical_operator = 'OR'">ИЛИ</button>
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
            <div class="constructor-item item-label label-then">ТО</div>
            <StSelect
              :model-value="action.action_type"
              :options="ACTION_TYPES"
              @update:model-value="onActionTypeChange(action, $event as string)"
            />
            <div class="constructor-item item-button-group">
              <button
                class="btn-icon btn-delete"
                title="Удалить действие"
                :disabled="rule.actions.length <= 1"
                @click="removeAction(a_index)"
              >
                <i class="pi pi-times" />
              </button>
            </div>
          </StGroup>

          <div v-if="action.action_type !== 'exclude'" class="action-content mt-2">
            <!-- extract_*: конструктор паттерна -->
            <StGroup v-if="EXTRACT_TYPES.includes(action.action_type)" class="group-auto-height">
              <div class="constructor-item item-pattern-editor">
                <ParserPatternEditor :blocks="action._action_blocks" context="then" />
              </div>
              <div v-if="a_index === rule.actions.length - 1" class="constructor-item item-button-group">
                <button class="btn-icon btn-add" title="Добавить действие" @click="addAction">
                  <i class="pi pi-plus" />
                </button>
              </div>
            </StGroup>

            <!-- assign_*: простое значение -->
            <StGroup v-else-if="ASSIGN_TYPES.includes(action.action_type)">
              <div class="constructor-item item-label">{{ getAssignActionLabel(action.action_type) }}</div>
              <input
                v-model="action.action_pattern"
                :type="['assign_episode', 'assign_season'].includes(action.action_type) ? 'number' : 'text'"
                class="constructor-item item-input"
                placeholder="Введите значение..."
              />
              <div v-if="a_index === rule.actions.length - 1" class="constructor-item item-button-group">
                <button class="btn-icon btn-add" title="Добавить действие" @click="addAction">
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
