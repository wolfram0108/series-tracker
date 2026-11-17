<script setup lang="ts">
import { ref, watch } from "vue"
import { useToast } from "primevue/usetoast"
import Button from "primevue/button"
import ParserRuleCard, { type EditableRule } from "./ParserRuleCard.vue"
import ParserTableCard from "./ParserTableCard.vue"
import { rowsOf, storedOf, type StoredTable } from "../../lib/ruleTable"
import type { PatternBlock } from "./ParserPatternEditor.vue"
import { useApi } from "../../composables/useApi"
import { useConfirm } from "../../composables/useConfirm"
import { api } from "../../api/client"
import { t } from "../../i18n"
import { useBusyBar } from "../../composables/useWindowBusy"
import { isExtract } from "../../lib/ruleVocabulary"
import LoadingMark from "../LoadingMark.vue"

// The rules of one profile of the constructor: the bar that adds a rule or a table, and the cards of the rules in their
// order. The section «Правила» and the tabs of the torrent editor hold the same editor; `lines` and `codes` are what the
// quick templates of a table are made of (the lines the rules read and the codes of their fields).
// `editor`: the rules of a tab of the tracks of a release — the actions of the editor (folders, language, type) are offered.
const props = defineProps<{ profileId: number; lines?: string[]; codes?: string[]; editor?: boolean; torrent?: boolean }>()
const { request } = useApi()
const confirm = useConfirm()
const toast = useToast()

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

const isLoading = ref(false)
useBusyBar(() => isLoading.value)
const rules = ref<EditableRule[]>([])
const openRuleId = ref<number | string | null>(null)

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
  if (!props.profileId) return
  isLoading.value = true
  rules.value = []
  openRuleId.value = null
  const raw = (await request(
    api.GET("/api/parser-profiles/{profile_id}/rules", {
      params: { path: { profile_id: props.profileId } },
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
    const profileId = props.profileId
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

watch(() => props.profileId, () => void loadRules(), { immediate: true })
</script>

<template>
  <div class="rules-editor">
    <!-- A rule added while the rules are read would be written over by them: the bar waits for the read. -->
    <div class="rules-toolbar">
      <Button :label="t('rules.rule.add')" icon="pi pi-plus-circle" size="small" :disabled="isLoading" @click="addRule" />
      <Button :label="t('rules.table.add')" icon="pi pi-table" size="small" severity="secondary" :disabled="isLoading" @click="addTable" />
    </div>
    <div v-if="isLoading" class="parser-loading"><LoadingMark /></div>
    <div v-else-if="rules.length === 0" class="parser-empty">{{ t("rules.rule.none") }}</div>
    <div v-else class="rules-list">
      <template v-for="(rule, index) in rules" :key="rule.id">
        <ParserTableCard
          v-if="rule.table" :rule="rule" :index="index" :total="rules.length" :is-open="openRuleId === rule.id"
          :lines="lines ?? []" :codes="codes ?? []" :editor="editor" :torrent="torrent"
          @toggle="toggleRule(rule.id)" @save="saveRule(rule)" @delete="deleteRule(rule)" @move="moveRule(index, $event)"
        />
        <ParserRuleCard
          v-else :rule="rule" :index="index" :total="rules.length" :is-open="openRuleId === rule.id" :editor="editor" :torrent="torrent"
          @toggle="toggleRule(rule.id)" @save="saveRule(rule)" @delete="deleteRule(rule)" @move="moveRule(index, $event)"
        />
      </template>
    </div>
  </div>
</template>
