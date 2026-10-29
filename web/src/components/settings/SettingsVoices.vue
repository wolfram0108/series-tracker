<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue"
import Button from "primevue/button"
import InputText from "primevue/inputtext"
import { useToast } from "primevue/usetoast"
import { api } from "../../api/client"
import type { components } from "../../api/schema"
import { useApi } from "../../composables/useApi"
import { t } from "../../i18n"
import { reasonText } from "../../lib/reason"

// Settings of the voices: groups of names that mean one voice-over, player names, source order, temporary files.
// Checking and saving belong to the voices module; this screen shows what applies and what a save was refused for.
type Draft = components["schemas"]["VoiceSettingsDraft"]
type View = components["schemas"]["VoiceSettingsView"]
type Problem = components["schemas"]["SettingProblem"]
type Groups = components["schemas"]["VoiceGroups"]
type Field = "priority" | "voice_aliases" | "player_aliases" | "temp_root" | "max_temp_episodes"
interface Row { id: number; from: string; to: string }

const { request } = useApi()
const toast = useToast()

let nextId = 0
const rowsOf = (pairs: Record<string, string>): Row[] =>
  Object.entries(pairs).map(([from, to]) => ({ id: nextId++, from, to }))

const loaded = ref(false)
const view = ref<View | null>(null)
const priority = ref<string[]>([])
const voices = ref<Row[]>([])
const players = ref<Row[]>([])
const tempRoot = ref("")
const maxTemp = ref<number | "">(0)
const newPlayer = ref("")
const problems = ref<Problem[]>([])
const saving = ref(false)

const namesText = ref("")
const groups = ref<Groups | null>(null)

/** The rows as the wire holds them. A name written twice is told apart here: a record would keep only one of them. */
function recordOf(rows: Row[], field: Field): { pairs: Record<string, string>; clashes: Problem[] } {
  const pairs: Record<string, string> = {}
  const clashes: Problem[] = []
  for (const row of rows) {
    if (!row.from.trim() && !row.to.trim()) continue
    const name = row.from.trim()
    if (name in pairs && pairs[name] !== row.to.trim()) {
      clashes.push({ field, detail: { code: "alias_conflict", params: { name, first: pairs[name], second: row.to.trim() } } })
    }
    pairs[name] = row.to.trim()
  }
  return { pairs, clashes }
}

const voicePairs = computed(() => recordOf(voices.value, "voice_aliases"))
const playerPairs = computed(() => recordOf(players.value, "player_aliases"))

const draft = computed<Draft>(() => ({
  priority: priority.value,
  voice_aliases: voicePairs.value.pairs,
  player_aliases: playerPairs.value.pairs,
  temp_root: tempRoot.value,
  max_temp_episodes: typeof maxTemp.value === "number" ? Math.trunc(maxTemp.value) : 0,
}))

const snapshot = ref("")
const dirty = computed(() => loaded.value && JSON.stringify(draft.value) !== snapshot.value)
const clientProblems = computed(() => [...voicePairs.value.clashes, ...playerPairs.value.clashes])

function apply(next: View) {
  view.value = next
  priority.value = [...next.priority]
  voices.value = rowsOf(next.voice_aliases)
  players.value = rowsOf(next.player_aliases)
  tempRoot.value = next.temp_root
  maxTemp.value = next.max_temp_episodes
  snapshot.value = JSON.stringify(draft.value)
  problems.value = []
  loaded.value = true
}

async function load() {
  const next = await request(api.GET("/api/voices/settings"), { errorMessage: t("voices.settings.loadFailed") })
  if (next) apply(next)
}

async function save() {
  if (clientProblems.value.length) {
    problems.value = clientProblems.value
    return
  }
  saving.value = true
  try {
    const reply = await request(api.PUT("/api/voices/settings", { body: draft.value }), {
      errorMessage: t("voices.settings.saveFailed"),
    })
    if (!reply) return
    if (reply.saved) {
      apply(reply.settings)
      toast.add({ severity: "success", summary: t("voices.settings.saved"), life: 2500 })
    } else {
      problems.value = reply.problems
      toast.add({ severity: "warn", summary: t("voices.settings.notSaved"), life: 4000 })
    }
  } finally {
    saving.value = false
  }
}

function discard() {
  if (view.value) apply(view.value)
}

// A problem belongs to the form as it was sent; once the form changes the marks are stale.
watch(draft, () => { problems.value = [] })

const problemsOf = (field: Field) => problems.value.filter((problem) => problem.field === field)
const rejected = (field: Field) => !!view.value?.rejected.includes(field)

function addRow(rows: Row[]) { rows.push({ id: nextId++, from: "", to: "" }) }
function removeRow(rows: Row[], id: number) { rows.splice(rows.findIndex((row) => row.id === id), 1) }

function move(index: number, by: -1 | 1) {
  const list = priority.value
  const target = index + by
  if (target < 0 || target >= list.length) return
  ;[list[index], list[target]] = [list[target], list[index]]
}
function addPlayer() {
  const name = newPlayer.value.trim()
  if (name) priority.value.push(name)
  newPlayer.value = ""
}
function resetPriority() {
  if (view.value) priority.value = [...view.value.defaults.priority]
}

async function showGroups() {
  const names = namesText.value.split("\n").map((line) => line.trim()).filter(Boolean)
  groups.value = await request(
    api.POST("/api/voices/groups/preview", {
      body: { names, voice_aliases: voicePairs.value.pairs, player_aliases: playerPairs.value.pairs },
    }),
    { errorMessage: t("voices.settings.loadFailed") },
  )
}
function fillKnown() { namesText.value = (view.value?.known_voices ?? []).join("\n") }

onMounted(load)
</script>

<template>
  <div class="settings-voices">
    <!-- Voice names that mean one voice-over -->
    <div class="modern-fieldset">
      <div class="fieldset-header"><i class="pi pi-users"></i> {{ t("voices.settings.voices.title") }}</div>
      <div class="fieldset-content">
        <p class="muted-hint">{{ t("voices.settings.voices.hint") }}</p>
        <p v-if="rejected('voice_aliases')" class="problem">{{ t("voices.settings.rejected") }}</p>
        <div v-if="!voices.length" class="muted-hint">{{ t("voices.settings.alias.empty") }}</div>
        <div v-for="row in voices" :key="row.id" class="pair-row">
          <InputText v-model="row.from" class="pair-input" :placeholder="t('voices.settings.alias.from')" :aria-label="t('voices.settings.alias.from')" />
          <i class="pi pi-arrow-right pair-arrow" aria-hidden="true"></i>
          <InputText v-model="row.to" class="pair-input" :placeholder="t('voices.settings.alias.to')" :aria-label="t('voices.settings.alias.to')" />
          <Button icon="pi pi-times" severity="danger" text rounded :title="t('voices.settings.alias.remove')" :aria-label="t('voices.settings.alias.remove')" @click="removeRow(voices, row.id)" />
        </div>
        <p v-for="(problem, index) in problemsOf('voice_aliases')" :key="index" class="problem">{{ reasonText(problem.detail) }}</p>
        <Button :label="t('voices.settings.alias.add')" icon="pi pi-plus" severity="secondary" size="small" @click="addRow(voices)" />
      </div>
    </div>

    <!-- Trying the rules on names, before they are saved -->
    <div class="modern-fieldset">
      <div class="fieldset-header"><i class="pi pi-sitemap"></i> {{ t("voices.settings.preview.title") }}</div>
      <div class="fieldset-content">
        <p class="muted-hint">{{ t("voices.settings.preview.hint") }}</p>
        <textarea v-model="namesText" class="names-input" rows="5" :placeholder="t('voices.settings.preview.placeholder')" :aria-label="t('voices.settings.preview.title')"></textarea>
        <div class="actions-row">
          <Button :label="t('voices.settings.preview.run')" icon="pi pi-search" @click="showGroups" />
          <Button :label="t('voices.settings.preview.known')" icon="pi pi-download" severity="secondary" :disabled="!view?.known_voices.length" @click="fillKnown" />
          <span v-if="loaded && !view?.known_voices.length" class="muted-hint">{{ t("voices.settings.preview.noKnown") }}</span>
        </div>
        <template v-if="groups">
          <p v-if="!groups.groups.length" class="muted-hint">{{ t("voices.settings.preview.empty") }}</p>
          <div v-for="group in groups.groups" :key="group.key" class="group-row">
            <b>{{ t("voices.settings.preview.group", { key: group.key }) }}</b>
            <span class="group-members">
              <span v-for="member in group.members" :key="member" class="group-member">{{ member }}</span>
            </span>
          </div>
          <p v-for="(problem, index) in groups.problems" :key="index" class="problem">{{ reasonText(problem.detail) }}</p>
        </template>
      </div>
    </div>

    <!-- Player kinds under different names -->
    <div class="modern-fieldset">
      <div class="fieldset-header"><i class="pi pi-play"></i> {{ t("voices.settings.players.title") }}</div>
      <div class="fieldset-content">
        <p class="muted-hint">{{ t("voices.settings.players.hint") }}</p>
        <p v-if="rejected('player_aliases')" class="problem">{{ t("voices.settings.rejected") }}</p>
        <div v-if="!players.length" class="muted-hint">{{ t("voices.settings.alias.empty") }}</div>
        <div v-for="row in players" :key="row.id" class="pair-row">
          <InputText v-model="row.from" class="pair-input" :placeholder="t('voices.settings.alias.from')" :aria-label="t('voices.settings.alias.from')" />
          <i class="pi pi-arrow-right pair-arrow" aria-hidden="true"></i>
          <InputText v-model="row.to" class="pair-input" :placeholder="t('voices.settings.alias.to')" :aria-label="t('voices.settings.alias.to')" />
          <Button icon="pi pi-times" severity="danger" text rounded :title="t('voices.settings.alias.remove')" :aria-label="t('voices.settings.alias.remove')" @click="removeRow(players, row.id)" />
        </div>
        <p v-for="(problem, index) in problemsOf('player_aliases')" :key="index" class="problem">{{ reasonText(problem.detail) }}</p>
        <Button :label="t('voices.settings.alias.add')" icon="pi pi-plus" severity="secondary" size="small" @click="addRow(players)" />
      </div>
    </div>

    <!-- Order of the sources -->
    <div class="modern-fieldset">
      <div class="fieldset-header"><i class="pi pi-sort-amount-down"></i> {{ t("voices.settings.priority.title") }}</div>
      <div class="fieldset-content">
        <p class="muted-hint">{{ t("voices.settings.priority.hint") }}</p>
        <p v-if="rejected('priority')" class="problem">{{ t("voices.settings.rejected") }}</p>
        <ol class="priority-list">
          <li v-for="(name, index) in priority" :key="`${index}:${name}`" class="priority-row">
            <span class="priority-place">{{ index + 1 }}</span>
            <span class="priority-name">{{ name }}</span>
            <Button icon="pi pi-arrow-up" text rounded :disabled="index === 0" :title="t('voices.settings.priority.up')" :aria-label="t('voices.settings.priority.up')" @click="move(index, -1)" />
            <Button icon="pi pi-arrow-down" text rounded :disabled="index === priority.length - 1" :title="t('voices.settings.priority.down')" :aria-label="t('voices.settings.priority.down')" @click="move(index, 1)" />
            <Button icon="pi pi-times" severity="danger" text rounded :title="t('voices.settings.priority.remove')" :aria-label="t('voices.settings.priority.remove')" @click="priority.splice(index, 1)" />
          </li>
        </ol>
        <p v-for="(problem, index) in problemsOf('priority')" :key="index" class="problem">{{ reasonText(problem.detail) }}</p>
        <div class="actions-row">
          <InputText v-model="newPlayer" class="pair-input" :placeholder="t('voices.settings.priority.addPlaceholder')" :aria-label="t('voices.settings.priority.add')" @keyup.enter="addPlayer" />
          <Button :label="t('voices.settings.priority.add')" icon="pi pi-plus" severity="secondary" size="small" @click="addPlayer" />
          <Button :label="t('voices.settings.priority.reset')" icon="pi pi-undo" severity="secondary" size="small" text @click="resetPriority" />
        </div>
      </div>
    </div>

    <!-- Temporary files -->
    <div class="modern-fieldset">
      <div class="fieldset-header"><i class="pi pi-folder"></i> {{ t("voices.settings.temp.title") }}</div>
      <div class="fieldset-content temp-grid">
        <label class="temp-field">
          <span>{{ t("voices.settings.temp.root") }}</span>
          <InputText v-model="tempRoot" :placeholder="view?.defaults.temp_root" />
          <span class="muted-hint">{{ t("voices.settings.temp.rootHint") }}</span>
          <span v-if="rejected('temp_root')" class="problem">{{ t("voices.settings.rejected") }}</span>
          <span v-for="(problem, index) in problemsOf('temp_root')" :key="index" class="problem">{{ reasonText(problem.detail) }}</span>
        </label>
        <label class="temp-field">
          <span>{{ t("voices.settings.temp.max") }}</span>
          <input v-model.number="maxTemp" class="p-inputtext num-input" type="number" min="0" step="1" />
          <span class="muted-hint">{{ t("voices.settings.temp.maxHint") }}</span>
          <span v-if="rejected('max_temp_episodes')" class="problem">{{ t("voices.settings.rejected") }}</span>
          <span v-for="(problem, index) in problemsOf('max_temp_episodes')" :key="index" class="problem">{{ reasonText(problem.detail) }}</span>
        </label>
      </div>
    </div>

    <!-- One save for the whole form -->
    <div class="save-bar">
      <Button :label="t('voices.settings.save')" icon="pi pi-check" :loading="saving" :disabled="!dirty" @click="save" />
      <Button :label="t('voices.settings.discard')" icon="pi pi-undo" severity="secondary" :disabled="!dirty" @click="discard" />
      <span v-if="dirty" class="muted-hint">{{ t("voices.settings.dirty") }}</span>
    </div>
  </div>
</template>

<style scoped>
.settings-voices { display: flex; flex-direction: column; gap: 1.2rem; }
.muted-hint { color: var(--text-muted); margin: 0 0 0.75rem; }
.problem { color: var(--color-red-1); margin: 0.4rem 0; }
.pair-row { display: flex; flex-wrap: wrap; align-items: center; gap: 0.5rem; margin-bottom: 0.5rem; }
.pair-input { flex: 1 1 12rem; min-width: 0; }
.pair-arrow { color: var(--text-muted); }
.names-input {
  width: 100%; box-sizing: border-box; font-family: var(--font-mono); padding: 0.6rem 0.8rem;
  border: 1px solid var(--border-color); border-radius: var(--border-radius); background: var(--bg-white); color: inherit;
}
.actions-row { display: flex; flex-wrap: wrap; align-items: center; gap: 0.6rem; margin: 0.75rem 0; }
.group-row { display: flex; flex-wrap: wrap; align-items: baseline; gap: 0.4rem 1rem; padding: 0.5rem 0; border-top: 1px solid var(--border-color); }
.group-members { display: inline-flex; flex-wrap: wrap; gap: 0.4rem; }
.group-member { padding: 0.15rem 0.6rem; background: var(--bg-light); border-radius: var(--border-radius); }
.priority-list { list-style: none; margin: 0 0 0.5rem; padding: 0; display: flex; flex-direction: column; gap: 0.35rem; }
.priority-row { display: flex; align-items: center; gap: 0.5rem; padding: 0.15rem 0.4rem 0.15rem 0.8rem; background: var(--bg-light); border-radius: var(--border-radius); }
.priority-place { color: var(--text-muted); min-width: 1.5rem; }
.priority-name { flex: 1 1 auto; font-weight: 600; }
.temp-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 18rem), 1fr)); gap: 1rem 1.5rem; }
.temp-field { display: flex; flex-direction: column; gap: 0.35rem; }
.num-input { max-width: 10rem; }
.save-bar { display: flex; flex-wrap: wrap; align-items: center; gap: 0.6rem; position: sticky; bottom: 0; padding: 0.6rem 0; background: var(--bg-white); }
</style>
