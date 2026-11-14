<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue"
import Button from "primevue/button"
import SheetField from "../SheetField.vue"
import StGroup from "../StGroup.vue"
import StInput from "../StInput.vue"
import StLabel from "../StLabel.vue"
import StSelect from "../StSelect.vue"
import type { components } from "../../api/schema"
import { useConfirm } from "../../composables/useConfirm"
import { t } from "../../i18n"
import { reasonText } from "../../lib/reason"
import { LANGUAGES } from "../../lib/languages"

// Groups of voice-overs. The top row (like step 1 of the VK filters) chooses, creates, renames and deletes a group. Under
// it lie the language of the chosen group, marked red until it is chosen, and its names; the last row of the names is the
// one a new name is written in.
type Problem = components["schemas"]["SettingProblem"]
type Groups = Record<string, string[]>
type Languages = Record<string, string>

const props = defineProps<{
  /** What the core holds. */
  groups: Groups
  /** The language of the voice-over of each group, as the core holds it. */
  languages: Languages
  busy: boolean
  /** Hands a change to the core; true when it was taken. */
  commit: (next: Groups, languages: Languages) => Promise<boolean>
  problems: Problem[]
  rejected: boolean
}>()
const confirm = useConfirm()

const selected = ref<string | null>(null)
const nameField = ref("")
const renaming = ref(false)
const editing = ref<number | null>(null)
const editText = ref("")
const newText = ref("")
const nameInput = ref<InstanceType<typeof StInput> | null>(null)
// What the name field says it is for: a new group, or the new name of the chosen one.
const nameLabel = computed(() => (renaming.value ? t("voices.settings.group.renameLabel") : t("voices.settings.group.new")))
const newInput = ref<HTMLInputElement | null>(null)

const names = computed(() => Object.keys(props.groups))
const rows = computed(() => (selected.value ? props.groups[selected.value] ?? [] : []))
const options = computed(() => [
  { label: t("voices.settings.group.select"), value: null as string | null },
  ...names.value.map((name) => ({ label: name, value: name as string | null })),
])
const languageOptions = LANGUAGES.map((code) => ({ label: t(`voices.language.${code}`), value: code as string | null }))

// A group another window removed is no longer the chosen one.
watch(() => props.groups, (groups) => {
  if (selected.value && !(selected.value in groups)) {
    selected.value = null
    renaming.value = false
    nameField.value = ""
  }
})

function choose(name: string | null) {
  selected.value = name
  renaming.value = false
  nameField.value = ""
  editing.value = null
  newText.value = ""
}

async function create() {
  const name = nameField.value.trim()
  if (!name) return
  if (name in props.groups || (await props.commit({ ...props.groups, [name]: [] }, props.languages))) choose(name)
}
async function setLanguage(language: string | null) {
  const name = selected.value
  if (props.busy || !name || !language || props.languages[name] === language) return
  await props.commit(props.groups, { ...props.languages, [name]: language })
}
function startRename() {
  if (!selected.value) return
  nameField.value = selected.value
  renaming.value = true
  void nextTick(() => nameInput.value?.focus())
}
function cancelRename() {
  renaming.value = false
  nameField.value = ""
}
async function applyRename() {
  const from = selected.value
  const to = nameField.value.trim()
  if (!from || !to) return
  if (to === from) return cancelRename()
  if (to in props.groups) return
  const renamed = Object.fromEntries(Object.entries(props.groups).map(([name, list]) => [name === from ? to : name, list]))
  const languages = Object.fromEntries(Object.entries(props.languages).map(([name, code]) => [name === from ? to : name, code]))
  if (await props.commit(renamed, languages)) {
    selected.value = to
    cancelRename()
  }
}
async function removeGroup() {
  const name = selected.value
  if (!name) return
  const answer = await confirm.open({
    title: t("voices.settings.group.deleteTitle"),
    message: t("voices.settings.group.deleteMessage", { group: name }),
  })
  if (!answer.confirmed) return
  const { [name]: _gone, ...rest } = props.groups
  const { [name]: _language, ...languages } = props.languages
  if (await props.commit(rest, languages)) choose(null)
}

const withRows = (list: string[]): Groups => ({ ...props.groups, [selected.value as string]: list })
const keep = (list: string[]) => props.commit(withRows(list), props.languages)

function startEdit(index: number) {
  editing.value = index
  editText.value = rows.value[index]
}
async function saveEdit() {
  const index = editing.value
  const name = editText.value.trim()
  if (index === null || !name) return
  if (await keep(rows.value.map((row, at) => (at === index ? name : row)))) editing.value = null
}
async function removeRow(index: number) {
  await keep(rows.value.filter((_, at) => at !== index))
}
async function addRow() {
  const name = newText.value.trim()
  if (!name) return
  if (await keep([...rows.value, name])) {
    newText.value = ""
    void nextTick(() => newInput.value?.focus())
  }
}
</script>

<template>
  <div class="groups-editor">
    <p v-if="rejected" class="sheet-problem">{{ t("voices.settings.rejected") }}</p>

    <!-- [group list][name: new or being edited][+][edit][delete] -->
    <StGroup>
      <StLabel icon="pi pi-users" :text="t('voices.settings.group.field')" />
      <StSelect :model-value="selected" :options="options" :placeholder="t('voices.settings.group.select')" @update:model-value="choose($event as string | null)" />
      <StInput
        ref="nameInput" v-model="nameField" :placeholder="nameLabel" :aria-label="nameLabel"
        @keyup.enter="renaming ? applyRename() : create()" @keydown.esc.prevent="cancelRename"
      />
      <div class="constructor-item item-button-group">
        <button v-if="!renaming" class="btn-icon btn-add" :disabled="busy || !nameField.trim()" :title="t('voices.settings.group.create')" :aria-label="t('voices.settings.group.create')" @click="create">
          <i class="pi pi-plus" />
        </button>
        <button v-else class="btn-icon btn-cancel" :title="t('voices.settings.group.cancel')" :aria-label="t('voices.settings.group.cancel')" @click="cancelRename">
          <i class="pi pi-times" />
        </button>
        <button v-if="!renaming" class="btn-icon btn-edit" :disabled="busy || !selected" :title="t('voices.settings.group.rename')" :aria-label="t('voices.settings.group.rename')" @click="startRename">
          <i class="pi pi-pencil" />
        </button>
        <button v-else class="btn-icon btn-confirm" :disabled="busy || !nameField.trim()" :title="t('voices.settings.group.apply')" :aria-label="t('voices.settings.group.apply')" @click="applyRename">
          <i class="pi pi-check" />
        </button>
        <button class="btn-icon btn-delete" :disabled="busy || !selected || renaming" :title="t('voices.settings.group.delete')" :aria-label="t('voices.settings.group.delete')" @click="removeGroup">
          <i class="pi pi-trash" />
        </button>
      </div>
    </StGroup>

    <!-- The language and the names of the chosen group; the last row is the one a new name is written in -->
    <div v-if="selected" class="names-area">
      <SheetField class="group-language" :label="t('voices.settings.group.language')" icon="pi pi-globe" :state="languages[selected] ? null : 'invalid'">
        <StSelect
          :model-value="languages[selected] ?? null" :options="languageOptions"
          :placeholder="t('voices.settings.group.languageNone')" :aria-label="t('voices.settings.group.language')"
          @update:model-value="setLanguage($event as string | null)"
        />
      </SheetField>
      <div v-for="(name, index) in rows" :key="`${index}:${name}`" class="name-row">
        <template v-if="editing !== index">
          <span class="name-text">{{ name }}</span>
          <Button icon="pi pi-pencil" text rounded :disabled="busy" :title="t('voices.settings.name.edit')" :aria-label="t('voices.settings.name.edit')" @click="startEdit(index)" />
          <Button icon="pi pi-trash" severity="danger" text rounded :disabled="busy" :title="t('voices.settings.name.remove')" :aria-label="t('voices.settings.name.remove')" @click="removeRow(index)" />
        </template>
        <template v-else>
          <input v-model="editText" class="name-input" type="text" @keyup.enter="saveEdit" @keydown.esc.prevent="editing = null" />
          <Button icon="pi pi-check" text rounded :disabled="busy || !editText.trim()" :title="t('voices.settings.name.save')" :aria-label="t('voices.settings.name.save')" @click="saveEdit" />
          <Button icon="pi pi-times" severity="secondary" text rounded :title="t('voices.settings.name.cancel')" :aria-label="t('voices.settings.name.cancel')" @click="editing = null" />
        </template>
      </div>
      <div class="name-row">
        <input ref="newInput" v-model="newText" class="name-input" type="text" :placeholder="t('voices.settings.name.new')" :aria-label="t('voices.settings.name.new')" @keyup.enter="addRow" />
        <Button icon="pi pi-check" text rounded :disabled="busy || !newText.trim()" :title="t('voices.settings.name.save')" :aria-label="t('voices.settings.name.save')" @click="addRow" />
      </div>
    </div>
    <p v-else class="sheet-hint">{{ names.length ? t("voices.settings.group.choose") : t("voices.settings.group.none") }}</p>

    <p v-for="(problem, index) in problems" :key="index" class="sheet-problem">{{ reasonText(problem.detail) }}</p>
  </div>
</template>

<style scoped>
.groups-editor { display: flex; flex-direction: column; gap: 0.6rem; }
.names-area { display: flex; flex-direction: column; gap: 0.35rem; }
.name-row { display: flex; align-items: center; gap: 0.5rem; padding: 0.15rem 0.4rem 0.15rem 0.8rem; background: var(--bg-light); border-radius: var(--border-radius); }
.name-text { flex: 1 1 auto; min-width: 0; font-weight: 600; overflow-wrap: anywhere; }
.name-input {
  flex: 1 1 auto; min-width: 0; padding: 0.4rem 0.7rem; color: inherit; background: var(--bg-white);
  border: 1px solid var(--border-color); border-radius: var(--border-radius);
}
</style>
