<script setup lang="ts">
import { ref, computed, watch, nextTick, onMounted } from "vue"
import { useToast } from "primevue/usetoast"
import Button from "primevue/button"
import StGroup from "../StGroup.vue"
import StInput from "../StInput.vue"
import StLabel from "../StLabel.vue"
import StSelect from "../StSelect.vue"
import ParserRuleCard, { type EditableRule } from "./ParserRuleCard.vue"
import ParserTableCard from "./ParserTableCard.vue"
import { rowsOf, storedOf, type StoredTable } from "../../lib/ruleTable"
import type { PatternBlock } from "./ParserPatternEditor.vue"
import ParserTest from "./ParserTest.vue"
import { useApi } from "../../composables/useApi"
import { useConfirm } from "../../composables/useConfirm"
import { api } from "../../api/client"
import { t } from "../../i18n"
import { isExtract } from "../../lib/ruleVocabulary"
import type { SourceAddress } from "../../lib/sources"

// Вкладка «Фильтры VK» окна Настроек — конфигуратор профилей парсера.
// Аккордеон трёх шагов (профили / правила / тест), как в оригинале
// (static/js/components/settingsParser.js). Step 2/3 видимы после выбора
// профиля. Вся API профилей/правил живёт здесь; карточка правила и тест —
// отдельные компоненты.

// initialProfileId / testInitialTitles — опциональны: заданы при встраивании
// раздела «Фильтры VK» вкладкой в окно добавления сериала (предвыбор профиля +
// список файлов раздачи в шаг 3). В Настройках не передаются → поведение как было.
// testFirst: при выборе профиля раскрывать сразу Шаг 3 (Тест) вместо Шага 2
// (окно добавления — цель «проверить нейминг»). В Настройках не передаётся → Шаг 2.
const props = defineProps<{
  initialProfileId?: number | null
  testInitialTitles?: string[]
  testInitialUrl?: string
  testFirst?: boolean
  /** The sources of the series the section stands in (its fields of the test offer them); none in the settings. */
  loadSources?: () => Promise<SourceAddress[]>
}>()
const emit = defineEmits<{ (e: "update:profileId", v: number | null): void }>()

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
  valid?: boolean
  error?: string | null
  conditions: RawCondition[]
  rule_table?: StoredTable | null
}

const profiles = ref<Profile[]>([])
const selectedProfileId = ref<number | null>(props.initialProfileId ?? null)
const newProfileName = ref("")
const isLoading = ref(false)
const rules = ref<EditableRule[]>([])
// The lines of the test (step 3) and the codes of the fields of a line: what the quick templates of a table are made of.
const testLines = ref<string[]>([])
const lineFields = ref<string[]>([])
const openRuleId = ref<number | string | null>(null)
const openStep = ref<"profiles" | "rules" | "test" | null>("profiles")

const editingProfileId = ref<number | null>(null)
const editingProfileName = ref("")
const editInput = ref<InstanceType<typeof StInput> | null>(null)

const profileOptions = computed(() => [
  { label: t("rules.profile.select"), value: null as number | null },
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
    api.GET("/api/parser-profiles"),
    { errorMessage: t("rules.profile.loadFailed") },
  )) as Profile[] | null
  if (data) profiles.value = data
  isLoading.value = false
}

async function createProfile(): Promise<void> {
  const name = newProfileName.value.trim()
  if (!name) return
  isLoading.value = true
  const data = (await request(
    api.POST("/api/parser-profiles", { body: { name } }),
    { errorMessage: t("rules.profile.createFailed") },
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
    }),
    { errorMessage: t("rules.profile.renameFailed") },
  )
  isLoading.value = false
  if (ok) {
    toast.add({ severity: "success", summary: t("rules.profile.toast"), detail: t("rules.profile.renamed"), life: 2500 })
    const keep = selectedProfileId.value
    await loadProfiles()
    selectedProfileId.value = keep
  }
  cancelEditing()
}
async function deleteProfile(): Promise<void> {
  if (!selectedProfileId.value) return
  const r = await confirm.open({
    title: t("rules.profile.deleteTitle"),
    message: t("rules.profile.deleteMessage", { profile: selectedProfileName.value }),
  })
  if (!r.confirmed) return
  isLoading.value = true
  const ok = await request(
    api.DELETE("/api/parser-profiles/{profile_id}", {
      params: { path: { profile_id: selectedProfileId.value } },
    }),
    { errorMessage: t("rules.profile.deleteFailed") },
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
    }),
    { errorMessage: t("rules.rule.loadFailed") },
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
      valid: rule.valid !== false,
      error: rule.error ?? null,
      conditions: (rule.conditions || []).map((c) => ({
        id: c.id,
        condition_type: c.condition_type,
        logical_operator: c.logical_operator,
        _blocks: parsePatternJson(c.pattern),
      })),
      actions: actions.map((a) => ({
        action_type: a.action_type,
        // The value of an assignment is text; a rule stored with a number (the core takes both) is read as its text.
        action_pattern: String(a.action_pattern ?? ""),
        _action_blocks: parsePatternJson(a.action_pattern),
      })),
      table: rule.rule_table ? { action_type: rule.rule_table.action_type, rows: rowsOf(rule.rule_table) } : null,
    }
  })
}

// A new rule «Таблица» starts as a new ordinary rule does: «ТО» excludes, «продолжать» is off.
function addTable(): void {
  const id = "new-" + Date.now()
  rules.value.push({
    id, name: t("rules.table.newName"), continue_after_match: false, is_new: true, conditions: [], actions: [],
    table: { action_type: "exclude", rows: [] },
  })
  openRuleId.value = id
  openStep.value = "rules"
}

function addRule(): void {
  const id = "new-" + Date.now()
  rules.value.push({
    id,
    name: t("rules.rule.newName"),
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
  if (rule.table) {
    const { action_type: actionType, rows } = rule.table
    return { name: rule.name, continue_after_match: !!rule.continue_after_match, action_pattern: "[]", conditions: [],
             rule_table: storedOf(actionType, rows, isExtract(actionType)) }
  }
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
    if (isExtract(a.action_type)) {
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
  let payload: Record<string, unknown>
  try {
    payload = prepareRuleForSave(rule)
  } catch (error) {
    // A rule the window could not put together is not saved, and the user is told so: a save never fails in silence.
    toast.add({ severity: "error", summary: t("rules.rule.toast"), detail: t("rules.rule.saveFailed"), life: 6000 })
    throw error
  }
  if (isNew) {
    const profileId = selectedProfileId.value
    if (profileId === null) return // a rule is made in a profile: with none chosen there is nowhere to save it
    const data = (await request(
      api.POST("/api/parser-profiles/{profile_id}/rules", {
        params: { path: { profile_id: profileId } },
        body: payload,
      }),
      { errorMessage: t("rules.rule.saveFailed") },
    )) as { success: boolean; id: number } | null
    if (data?.id) {
      toast.add({ severity: "success", summary: t("rules.rule.toast"), detail: t("rules.rule.saved"), life: 2000 })
      await loadRules()
      openRuleId.value = data.id
    }
  } else {
    if (typeof rule.id !== "number") return // a saved rule has the number of its row
    const ok = await request(
      api.PUT("/api/parser-rules/{rule_id}", {
        params: { path: { rule_id: rule.id } },
        body: payload,
      }),
      { errorMessage: t("rules.rule.saveFailed") },
    )
    if (ok) toast.add({ severity: "success", summary: t("rules.rule.toast"), detail: t("rules.rule.saved"), life: 2000 })
  }
}

async function deleteRule(rule: EditableRule): Promise<void> {
  if (typeof rule.id === "string" && rule.id.startsWith("new-")) {
    rules.value = rules.value.filter((r) => r.id !== rule.id)
    return
  }
  if (typeof rule.id !== "number") return
  const r = await confirm.open({ title: t("rules.rule.deleteTitle"), message: t("rules.rule.deleteMessage") })
  if (!r.confirmed) return
  const ok = await request(
    api.DELETE("/api/parser-rules/{rule_id}", {
      params: { path: { rule_id: rule.id } },
    }),
    { errorMessage: t("rules.rule.deleteFailed") },
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
    api.POST("/api/parser-rules/reorder", { body: orderedIds }),
    { errorMessage: t("rules.rule.reorderFailed") },
  )
  if (!ok) loadRules()
}

function toggleRule(id: number | string): void {
  openRuleId.value = openRuleId.value === id ? null : id
}

watch(selectedProfileId, (id) => {
  if (id) {
    loadRules()
    openStep.value = props.testFirst ? "test" : "rules"
  } else {
    rules.value = []
  }
  emit("update:profileId", id)  // синхронизация с формой окна добавления
})

// внешняя смена профиля (форма окна добавления) → отразить в разделе
watch(
  () => props.initialProfileId,
  (v) => {
    if (v !== undefined && v !== selectedProfileId.value) selectedProfileId.value = v ?? null
  },
)

async function loadLineFields(): Promise<void> {
  const codes = await request(api.GET("/api/rules/line-fields"))
  if (codes) lineFields.value = codes
}

onMounted(() => {
  void loadProfiles()
  void loadLineFields()
})
</script>

<template>
  <div class="parser-accordion">
    <!-- Шаг 1: профили -->
    <div class="accordion-item">
      <button class="accordion-button" :class="{ active: openStep === 'profiles' }" @click="toggleStep('profiles')">
        <span>{{ t("rules.step.profiles") }}</span>
        <i class="pi pi-chevron-down acc-chevron" />
      </button>
      <div v-if="openStep === 'profiles'" class="accordion-body">
        <p class="parser-hint">{{ t("rules.step.profiles.hint") }}</p>
        <StGroup>
          <template v-if="!editingProfileId">
            <StLabel icon="pi pi-user" :text="t('rules.profile.field')" />
            <StSelect v-model="selectedProfileId" :options="profileOptions" :placeholder="t('rules.profile.select')" />
            <StInput v-model="newProfileName" :placeholder="t('rules.profile.new')" :aria-label="t('rules.profile.new')" @keyup.enter="createProfile" />
            <div class="constructor-item item-button-group">
              <button class="btn-icon btn-add" :disabled="!newProfileName.trim() || isLoading" :title="t('rules.profile.create')" :aria-label="t('rules.profile.create')" @click="createProfile">
                <i class="pi pi-plus" />
              </button>
              <button class="btn-icon btn-edit" :disabled="!selectedProfileId || isLoading" :title="t('rules.profile.rename')" :aria-label="t('rules.profile.rename')" @click="startEditingProfile">
                <i class="pi pi-pencil" />
              </button>
              <button class="btn-icon btn-delete" :disabled="!selectedProfileId || isLoading" :title="t('rules.profile.delete')" :aria-label="t('rules.profile.delete')" @click="deleteProfile">
                <i class="pi pi-trash" />
              </button>
            </div>
          </template>
          <template v-else>
            <StLabel icon="pi pi-pencil" :text="t('rules.profile.renameLabel')" />
            <StInput ref="editInput" v-model="editingProfileName" :aria-label="t('rules.profile.renameLabel')" @keyup.enter="saveProfileName" @keydown.esc.prevent="cancelEditing" />
            <div class="constructor-item item-button-group">
              <button class="btn-icon btn-confirm" :disabled="!editingProfileName.trim()" :title="t('rules.profile.apply')" :aria-label="t('rules.profile.apply')" @click="saveProfileName">
                <i class="pi pi-check" />
              </button>
              <button class="btn-icon btn-cancel" :title="t('rules.profile.cancel')" :aria-label="t('rules.profile.cancel')" @click="cancelEditing">
                <i class="pi pi-times" />
              </button>
            </div>
          </template>
        </StGroup>
      </div>
    </div>

    <template v-if="selectedProfileId">
      <!-- Шаг 2: правила -->
      <div class="accordion-item">
        <button class="accordion-button" :class="{ active: openStep === 'rules' }" @click="toggleStep('rules')">
          <span>{{ t("rules.step.rules", { profile: selectedProfileName }) }}</span>
          <i class="pi pi-chevron-down acc-chevron" />
        </button>
        <div v-if="openStep === 'rules'" class="accordion-body">
          <div class="rules-toolbar">
            <Button :label="t('rules.rule.add')" icon="pi pi-plus-circle" size="small" @click="addRule" />
            <Button :label="t('rules.table.add')" icon="pi pi-table" size="small" severity="secondary" @click="addTable" />
          </div>
          <div v-if="isLoading" class="parser-loading"><i class="pi pi-spin pi-spinner" style="font-size: 1.6rem" /></div>
          <div v-else-if="rules.length === 0" class="parser-empty">{{ t("rules.rule.none") }}</div>
          <div v-else class="rules-list">
            <template v-for="(rule, index) in rules" :key="rule.id">
              <ParserTableCard
                v-if="rule.table"
                :rule="rule"
                :index="index"
                :total="rules.length"
                :is-open="openRuleId === rule.id"
                :lines="testLines"
                :codes="lineFields"
                @toggle="toggleRule(rule.id)"
                @save="saveRule(rule)"
                @delete="deleteRule(rule)"
                @move="moveRule(index, $event)"
              />
              <ParserRuleCard
                v-else
                :rule="rule"
                :index="index"
                :total="rules.length"
                :is-open="openRuleId === rule.id"
                @toggle="toggleRule(rule.id)"
                @save="saveRule(rule)"
                @delete="deleteRule(rule)"
                @move="moveRule(index, $event)"
              />
            </template>
          </div>
        </div>
      </div>

      <!-- Шаг 3: тест -->
      <div class="accordion-item">
        <button class="accordion-button" :class="{ active: openStep === 'test' }" @click="toggleStep('test')">
          <span>{{ t("rules.step.test") }}</span>
          <i class="pi pi-chevron-down acc-chevron" />
        </button>
        <div v-show="openStep === 'test'" class="accordion-body">
          <ParserTest
            :profile-id="selectedProfileId"
            :initial-titles="testInitialTitles"
            :initial-url="testInitialUrl"
            :load-sources="loadSources"
            @lines="testLines = $event"
          />
        </div>
      </div>
    </template>
  </div>
</template>
