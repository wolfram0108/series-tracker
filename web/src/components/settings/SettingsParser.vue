<script setup lang="ts">
import { ref, computed, watch, nextTick, onMounted } from "vue"
import { useToast } from "primevue/usetoast"
import Button from "primevue/button"
import StGroup from "../StGroup.vue"
import StIcon from "../StIcon.vue"
import StSelect from "../StSelect.vue"
import ParserRuleCard, { type EditableRule } from "./ParserRuleCard.vue"
import type { PatternBlock } from "./ParserPatternEditor.vue"
import ParserTest from "./ParserTest.vue"
import { useApi } from "../../composables/useApi"
import { useConfirm } from "../../composables/useConfirm"
import { api } from "../../api/client"

// Вкладка «Фильтры VK» окна Настроек — конфигуратор профилей парсера.
// Аккордеон трёх шагов (профили / правила / тест), как в оригинале
// (static/js/components/settingsParser.js). Step 2/3 видимы после выбора
// профиля. Вся API профилей/правил живёт здесь; карточка правила и тест —
// отдельные компоненты.

const { request } = useApi()
const confirm = useConfirm()
const toast = useToast()

interface Profile { id: number; name: string; preferred_voiceovers?: string }
interface RawCondition { id?: number; condition_type: string; pattern: string; logical_operator: string }
interface RawRule {
  id: number
  name: string
  priority: number
  action_pattern: string
  continue_after_match: boolean | number
  conditions: RawCondition[]
}

const EXTRACT_TYPES = ["extract_single", "extract_range", "extract_season"]

const profiles = ref<Profile[]>([])
const selectedProfileId = ref<number | null>(null)
const newProfileName = ref("")
const isLoading = ref(false)
const rules = ref<EditableRule[]>([])
const openRuleId = ref<number | string | null>(null)
const openStep = ref<"profiles" | "rules" | "test" | null>("profiles")

const editingProfileId = ref<number | null>(null)
const editingProfileName = ref("")
const editInput = ref<HTMLInputElement | null>(null)

const profileOptions = computed(() => [
  { label: "-- Выберите профиль --", value: null as number | null },
  ...profiles.value.map((p) => ({ label: p.name, value: p.id as number | null })),
])
const selectedProfileName = computed(
  () => profiles.value.find((p) => p.id === selectedProfileId.value)?.name ?? "",
)

function toggleStep(s: "profiles" | "rules" | "test"): void {
  openStep.value = openStep.value === s ? null : s
}

// --- Профили ----------------------------------------------------------
async function loadProfiles(): Promise<void> {
  isLoading.value = true
  const data = (await request(
    api.GET("/api/parser-profiles") as never,
    { errorMessage: "Ошибка загрузки профилей" },
  )) as Profile[] | null
  if (data) profiles.value = data
  isLoading.value = false
}

async function createProfile(): Promise<void> {
  if (!newProfileName.value) return
  isLoading.value = true
  const data = (await request(
    api.POST("/api/parser-profiles", { body: { name: newProfileName.value } } as never),
    { errorMessage: "Ошибка создания профиля" },
  )) as { success: boolean; id: number } | null
  isLoading.value = false
  if (data?.id) {
    newProfileName.value = ""
    await loadProfiles()
    selectedProfileId.value = data.id
  }
}

function startEditingProfile(): void {
  if (!selectedProfileId.value) return
  editingProfileId.value = selectedProfileId.value
  editingProfileName.value = selectedProfileName.value
  nextTick(() => editInput.value?.focus())
}
function cancelEditing(): void {
  editingProfileId.value = null
  editingProfileName.value = ""
}
async function saveProfileName(): Promise<void> {
  const name = editingProfileName.value.trim()
  if (!name || !editingProfileId.value) return cancelEditing()
  const current = profiles.value.find((p) => p.id === editingProfileId.value)
  if (current && current.name === name) return cancelEditing()
  isLoading.value = true
  const ok = await request(
    api.PUT("/api/parser-profiles/{profile_id}", {
      params: { path: { profile_id: editingProfileId.value } },
      body: { name },
    } as never),
    { errorMessage: "Ошибка переименования" },
  )
  isLoading.value = false
  if (ok) {
    toast.add({ severity: "success", summary: "Профиль", detail: "Переименован", life: 2500 })
    const keep = selectedProfileId.value
    await loadProfiles()
    selectedProfileId.value = keep
  }
  cancelEditing()
}
async function deleteProfile(): Promise<void> {
  if (!selectedProfileId.value) return
  const r = await confirm.open({
    title: "Удаление профиля",
    message: `Удалить профиль «${selectedProfileName.value}» и все его правила?`,
  })
  if (!r.confirmed) return
  isLoading.value = true
  const ok = await request(
    api.DELETE("/api/parser-profiles/{profile_id}", {
      params: { path: { profile_id: selectedProfileId.value } },
    } as never),
    { errorMessage: "Ошибка удаления профиля" },
  )
  isLoading.value = false
  if (ok) {
    selectedProfileId.value = null
    await loadProfiles()
  }
}

// --- Правила ----------------------------------------------------------
function parsePatternJson(jsonString?: string): PatternBlock[] {
  try {
    if (!jsonString) return []
    const blocks = JSON.parse(jsonString)
    return Array.isArray(blocks)
      ? blocks.map((b) => ({ ...b, id: Date.now() + Math.random() }))
      : []
  } catch {
    return []
  }
}

async function loadRules(): Promise<void> {
  if (!selectedProfileId.value) return
  isLoading.value = true
  rules.value = []
  openRuleId.value = null
  const raw = (await request(
    api.GET("/api/parser-profiles/{profile_id}/rules", {
      params: { path: { profile_id: selectedProfileId.value } },
    } as never),
    { errorMessage: "Ошибка загрузки правил" },
  )) as RawRule[] | null
  isLoading.value = false
  if (!raw) return
  rules.value = raw.map((rule) => {
    let actions: Array<{ action_type: string; action_pattern: string }> = []
    try {
      const parsed = JSON.parse(rule.action_pattern || "[]")
      if (Array.isArray(parsed)) actions = parsed
    } catch {
      actions = []
    }
    return {
      id: rule.id,
      name: rule.name,
      continue_after_match: !!rule.continue_after_match,
      conditions: (rule.conditions || []).map((c) => ({
        id: c.id,
        condition_type: c.condition_type,
        logical_operator: c.logical_operator,
        _blocks: parsePatternJson(c.pattern),
      })),
      actions: actions.map((a) => ({
        action_type: a.action_type,
        action_pattern: a.action_pattern ?? "",
        _action_blocks: parsePatternJson(a.action_pattern),
      })),
    }
  })
}

function addRule(): void {
  const id = "new-" + Date.now()
  rules.value.push({
    id,
    name: "Новое правило",
    continue_after_match: false,
    is_new: true,
    conditions: [
      { condition_type: "contains", logical_operator: "AND", _blocks: [{ type: "text", value: "", id: Date.now() }] },
    ],
    actions: [{ action_type: "exclude", action_pattern: "[]", _action_blocks: [] }],
  })
  openRuleId.value = id
  openStep.value = "rules"
}

function prepareRuleForSave(rule: EditableRule): Record<string, unknown> {
  const conditions = rule.conditions.map((c) => ({
    condition_type: c.condition_type,
    logical_operator: c.logical_operator,
    pattern: JSON.stringify((c._blocks || []).map(({ id, ...rest }) => rest)),
  }))
  const actions = (rule.actions || []).map((a) => {
    const saved: { action_type: string; action_pattern: string } = {
      action_type: a.action_type,
      action_pattern: a.action_pattern ?? "",
    }
    if (EXTRACT_TYPES.includes(a.action_type)) {
      saved.action_pattern = JSON.stringify((a._action_blocks || []).map(({ id, ...rest }) => rest))
    }
    return saved
  })
  return {
    name: rule.name,
    continue_after_match: !!rule.continue_after_match,
    action_pattern: JSON.stringify(actions),
    conditions,
  }
}

async function saveRule(rule: EditableRule): Promise<void> {
  const isNew = !!rule.is_new
  const payload = prepareRuleForSave(rule)
  if (isNew) {
    const data = (await request(
      api.POST("/api/parser-profiles/{profile_id}/rules", {
        params: { path: { profile_id: selectedProfileId.value } },
        body: payload,
      } as never),
      { errorMessage: "Ошибка сохранения правила" },
    )) as { success: boolean; id: number } | null
    if (data?.id) {
      toast.add({ severity: "success", summary: "Правило", detail: "Сохранено", life: 2000 })
      await loadRules()
      openRuleId.value = data.id
    }
  } else {
    const ok = await request(
      api.PUT("/api/parser-rules/{rule_id}", {
        params: { path: { rule_id: rule.id } },
        body: payload,
      } as never),
      { errorMessage: "Ошибка сохранения правила" },
    )
    if (ok) toast.add({ severity: "success", summary: "Правило", detail: "Сохранено", life: 2000 })
  }
}

async function deleteRule(rule: EditableRule): Promise<void> {
  if (typeof rule.id === "string" && rule.id.startsWith("new-")) {
    rules.value = rules.value.filter((r) => r.id !== rule.id)
    return
  }
  const r = await confirm.open({ title: "Удаление правила", message: "Удалить это правило?" })
  if (!r.confirmed) return
  const ok = await request(
    api.DELETE("/api/parser-rules/{rule_id}", {
      params: { path: { rule_id: rule.id } },
    } as never),
    { errorMessage: "Ошибка удаления правила" },
  )
  if (ok) loadRules()
}

async function moveRule(index: number, dir: -1 | 1): Promise<void> {
  const other = index + dir
  if (other < 0 || other >= rules.value.length) return
  const arr = [...rules.value]
  ;[arr[index], arr[other]] = [arr[other], arr[index]]
  rules.value = arr
  const orderedIds = arr.filter((r) => !r.is_new).map((r) => r.id)
  if (orderedIds.length < 2) return
  const ok = await request(
    api.POST("/api/parser-rules/reorder", { body: orderedIds } as never),
    { errorMessage: "Ошибка изменения порядка" },
  )
  if (!ok) loadRules()
}

function toggleRule(id: number | string): void {
  openRuleId.value = openRuleId.value === id ? null : id
}

watch(selectedProfileId, (id) => {
  if (id) {
    loadRules()
    openStep.value = "rules"
  } else {
    rules.value = []
  }
})

onMounted(loadProfiles)
</script>

<template>
  <div class="parser-accordion">
    <!-- Шаг 1: профили -->
    <div class="accordion-item">
      <button class="accordion-button" :class="{ active: openStep === 'profiles' }" @click="toggleStep('profiles')">
        <span>Шаг 1: Управление профилями парсера</span>
        <i class="pi pi-chevron-down acc-chevron" />
      </button>
      <div v-if="openStep === 'profiles'" class="accordion-body">
        <p class="parser-hint">
          Выберите профиль для редактирования или создайте новый. Правила и тестирование появятся ниже после выбора профиля.
        </p>
        <div class="field-group">
          <StGroup>
            <template v-if="!editingProfileId">
              <StIcon icon="pi pi-user" />
              <StSelect
                v-model="selectedProfileId"
                :options="profileOptions"
                placeholder="-- Выберите профиль --"
              />
              <div class="constructor-item item-floating-label">
                <input
                  id="new-profile-name"
                  v-model.trim="newProfileName"
                  type="text"
                  class="item-input"
                  placeholder=" "
                  @keyup.enter="createProfile"
                />
                <label for="new-profile-name">Имя нового профиля...</label>
              </div>
              <div class="constructor-item item-button-group">
                <button class="btn-icon btn-add" :disabled="!newProfileName || isLoading" title="Создать" @click="createProfile">
                  <i class="pi pi-plus" />
                </button>
                <button class="btn-icon btn-edit" :disabled="!selectedProfileId || isLoading" title="Переименовать" @click="startEditingProfile">
                  <i class="pi pi-pencil" />
                </button>
                <button class="btn-icon btn-delete" :disabled="!selectedProfileId || isLoading" title="Удалить" @click="deleteProfile">
                  <i class="pi pi-trash" />
                </button>
              </div>
            </template>
            <template v-else>
              <div class="constructor-item item-floating-label">
                <input
                  ref="editInput"
                  v-model.trim="editingProfileName"
                  type="text"
                  class="item-input"
                  placeholder=" "
                  @keyup.enter="saveProfileName"
                  @keyup.esc="cancelEditing"
                />
                <label>Новое имя профиля...</label>
              </div>
              <div class="constructor-item item-button-group">
                <button class="btn-icon btn-confirm" :disabled="!editingProfileName" title="Сохранить" @click="saveProfileName">
                  <i class="pi pi-check" />
                </button>
                <button class="btn-icon btn-cancel" title="Отмена" @click="cancelEditing">
                  <i class="pi pi-times" />
                </button>
              </div>
            </template>
          </StGroup>
        </div>
      </div>
    </div>

    <template v-if="selectedProfileId">
      <!-- Шаг 2: правила -->
      <div class="accordion-item">
        <button class="accordion-button" :class="{ active: openStep === 'rules' }" @click="toggleStep('rules')">
          <span>Шаг 2: Правила для профиля «{{ selectedProfileName }}»</span>
          <i class="pi pi-chevron-down acc-chevron" />
        </button>
        <div v-if="openStep === 'rules'" class="accordion-body">
          <div class="rules-toolbar">
            <Button label="Добавить правило" icon="pi pi-plus-circle" size="small" @click="addRule" />
          </div>
          <div v-if="isLoading" class="parser-loading"><i class="pi pi-spin pi-spinner" style="font-size: 1.6rem" /></div>
          <div v-else-if="rules.length === 0" class="parser-empty">Нет правил для этого профиля. Добавьте первое.</div>
          <div v-else class="rules-list">
            <ParserRuleCard
              v-for="(rule, index) in rules"
              :key="rule.id"
              :rule="rule"
              :index="index"
              :total="rules.length"
              :is-open="openRuleId === rule.id"
              @toggle="toggleRule(rule.id)"
              @save="saveRule(rule)"
              @delete="deleteRule(rule)"
              @move="moveRule(index, $event)"
            />
          </div>
        </div>
      </div>

      <!-- Шаг 3: тест -->
      <div class="accordion-item">
        <button class="accordion-button" :class="{ active: openStep === 'test' }" @click="toggleStep('test')">
          <span>Шаг 3: Тестирование профиля</span>
          <i class="pi pi-chevron-down acc-chevron" />
        </button>
        <div v-if="openStep === 'test'" class="accordion-body">
          <ParserTest :key="selectedProfileId" :profile-id="selectedProfileId" />
        </div>
      </div>
    </template>
  </div>
</template>
