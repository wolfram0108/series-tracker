<script setup lang="ts">
import { computed, onMounted, provide, ref, watch } from "vue"
import Button from "primevue/button"
import ToggleSwitch from "primevue/toggleswitch"
import { api } from "../../api/client"
import type { components } from "../../api/schema"
import { useApi } from "../../composables/useApi"
import { useSSE } from "../../composables/useSSE"
import { t } from "../../i18n"
import { CHANGE_ANSWER, nextAnswer } from "../../lib/changeAnswer"
import { reasonText } from "../../lib/reason"
import { useVoicesStore } from "../../stores/voices"
import OrderList from "../OrderList.vue"
import SheetRow from "../SheetRow.vue"
import VoiceGroupsEditor from "./VoiceGroupsEditor.vue"
import LoadGate from "../LoadGate.vue"
import { useLoad } from "../../composables/useLoad"

// Settings of the voices: groups of voice-overs and the order of the sources. Every action goes to the core at once and
// the screen shows what the core holds, so an action the core refuses is shown with its reason and snaps back.
type View = components["schemas"]["VoiceSettingsView"]
type Problem = components["schemas"]["SettingProblem"]
type Facts = components["schemas"]["PlayerFacts"]
type Groups = Record<string, string[]>

const { request } = useApi()
const voicesStore = useVoicesStore()

const view = ref<View | null>(null)
// Where each player of the order is among the sources of the series: asked when the screen opens.
const facts = ref<Record<string, Facts>>({})
const problems = ref<Problem[]>([])
const busy = ref(false)
const answer = ref({ n: 0, ok: true })
provide(CHANGE_ANSWER, answer)

async function load(): Promise<boolean> {
  const [next, players] = await Promise.all([
    request(api.GET("/api/voices/settings"), { errorMessage: t("voices.settings.loadFailed") }),
    request(api.GET("/api/voices/players"), { errorMessage: t("voices.settings.priority.playersFailed") }),
  ])
  if (next) view.value = next
  if (players) facts.value = Object.fromEntries((players.players ?? []).map((each) => [each.name, each]))
  return !!next
}
const gate = useLoad(load)

type Change = { priority?: string[]; players_off?: string[]; voice_groups?: Groups; voice_languages?: Record<string, string> }

async function commit(change: Change): Promise<boolean> {
  if (!view.value) return false
  busy.value = true
  try {
    const reply = await request(
      api.PUT("/api/voices/settings", {
        body: { priority: view.value.priority, players_off: view.value.players_off ?? [],
                voice_groups: view.value.voice_groups, voice_languages: view.value.voice_languages ?? {}, ...change },
      }),
      { errorMessage: t("voices.settings.saveFailed") },
    )
    if (reply) {
      view.value = reply.settings
      problems.value = reply.problems
    }
    answer.value = nextAnswer(answer.value, !!reply?.saved)
    return !!reply?.saved
  } finally {
    busy.value = false
  }
}

// The core announces every save, also one made in another window; a connection that came back may have missed one.
watch(() => voicesStore.settings, (next) => {
  if (next) view.value = next
})
watch(useSSE().connected, (up) => {
  if (up) void gate.run()
})

const problemsOf = (field: string) => problems.value.filter((problem) => problem.field === field)
const rejected = (field: string) => !!view.value?.rejected.includes(field)

const order = computed(() => view.value?.priority ?? [])

const keyOf = (name: string): string => name
function resetOrder() {
  if (view.value) void commit({ priority: [...view.value.default_priority] })
}

const off = computed(() => new Set(view.value?.players_off ?? []))
function switchPlayer(name: string, on: boolean) {
  const rest = (view.value?.players_off ?? []).filter((each) => each !== name)
  void commit({ players_off: on ? rest : [...rest, name] })
}
const nameOf = (name: string): string => view.value?.player_labels?.[name] ?? facts.value[name]?.label ?? name

onMounted(gate.run)
</script>

<template>
  <LoadGate :state="gate.state.value" :rows="4" @retry="gate.run">
    <div class="sheet settings-voices">
      <SheetRow :caption="t('voices.settings.voices.title')">
        <p class="sheet-hint">{{ t("voices.settings.voices.hint") }}</p>
        <VoiceGroupsEditor
          v-if="view"
          :groups="view.voice_groups"
          :busy="busy"
          :languages="view.voice_languages ?? {}"
          :commit="(next: Groups, languages: Record<string, string>) => commit({ voice_groups: next, voice_languages: languages })"
          :problems="[...problemsOf('voice_groups'), ...problemsOf('voice_languages')]"
          :rejected="rejected('voice_groups')"
        />
      </SheetRow>

      <SheetRow :caption="t('voices.settings.priority.title')">
        <p class="sheet-hint">{{ t("voices.settings.priority.hint") }}</p>
        <p v-if="rejected('priority')" class="sheet-problem">{{ t("voices.settings.rejected") }}</p>
        <div class="priority">
          <div class="priority-head" aria-hidden="true">
            <span></span>
            <span>{{ t("voices.settings.priority.col.place") }}</span>
            <span>{{ t("voices.settings.priority.col.name") }}</span>
            <span>{{ t("voices.settings.priority.col.kind") }}</span>
            <span>{{ t("voices.settings.priority.col.height") }}</span>
            <span>{{ t("voices.settings.priority.col.where") }}</span>
            <span class="priority-on">{{ t("voices.settings.priority.col.on") }}</span>
          </div>
          <OrderList class="priority-list" :items="order" :item-key="keyOf" :busy="busy" handle=".order-handle"
                     @order="commit({ priority: $event })">
            <template #item="{ item, index, step }">
              <li class="priority-row" :class="{ 'is-off': off.has(item) }" :data-player="item">
                <button type="button" class="order-handle" :disabled="busy" :aria-label="t('voices.settings.priority.drag')" @keydown="step">
                  <i class="pi pi-bars" aria-hidden="true" />
                </button>
                <span class="priority-place">{{ index + 1 }}</span>
                <span class="priority-name">{{ nameOf(item) }}</span>
                <span class="priority-meta">
                <span class="priority-kind">
                  <span v-if="facts[item]" class="kind" :class="`kind-${facts[item].kind}`">{{ t(`voices.settings.priority.kind.${facts[item].kind}`) }}</span>
                </span>
                <span class="priority-height">
                  <template v-if="facts[item]?.height">
                    <span class="height-cell">{{ t("voices.settings.priority.height", { height: facts[item].height ?? "" }) }}</span>
                    <span class="height-card">{{ t("voices.settings.priority.upTo", { height: facts[item].height ?? "" }) }}</span>
                  </template>
                </span>
                </span>
                <span class="priority-where">
                  <span v-for="place in facts[item]?.places ?? []" :key="place.name" class="place" :class="{ zero: !place.sources }">
                    {{ place.label }} <b>{{ place.sources }}</b>
                  </span>
                  <span v-if="facts[item] && !facts[item].places?.length" class="none">{{ t("voices.settings.priority.noSources") }}</span>
                </span>
                <span class="priority-on">
                  <ToggleSwitch :model-value="!off.has(item)" :disabled="busy"
                                :aria-label="t(off.has(item) ? 'voices.settings.priority.off' : 'voices.settings.priority.on', { player: nameOf(item) })"
                                @update:model-value="switchPlayer(item, $event)" />
                </span>
              </li>
            </template>
          </OrderList>
        </div>
        <p v-for="(problem, index) in [...problemsOf('priority'), ...problemsOf('players_off')]" :key="index" class="sheet-problem">{{ reasonText(problem.detail) }}</p>
        <div class="actions-row">
          <Button :label="t('voices.settings.priority.reset')" icon="pi pi-undo" severity="secondary" size="small" text :disabled="busy" @click="resetOrder" />
        </div>
      </SheetRow>
    </div>
  </LoadGate>
</template>

<style scoped>
.actions-row { display: flex; flex-wrap: wrap; align-items: center; gap: 0.6rem; margin: 0.75rem 0 0; }
/* One grid for the head and every row, so the columns line up; a narrow section turns each row into a card. */
.priority {
  container-type: inline-size;
  --priority-columns: 2.25rem 2rem minmax(6rem, 1.1fr) 9.5rem 4rem minmax(0, 3fr) 3.5rem;
  border: 1px solid var(--border-color); border-radius: var(--border-radius); overflow: hidden; margin: 0 0 0.5rem;
}
.priority-head, .priority-row { display: grid; grid-template-columns: var(--priority-columns); align-items: center; column-gap: 0.5rem; padding: 0.3rem 0.6rem; }
.priority-head { background: var(--bg-light); font-size: 0.75rem; letter-spacing: 0.03em; text-transform: uppercase; color: var(--color-gray-600); }
.priority-list { list-style: none; margin: 0; padding: 0; }
.priority-row { border-top: 1px solid var(--border-color); min-height: 2.75rem; }
.priority-place { color: var(--text-muted); font-variant-numeric: tabular-nums; }
.priority-name { font-weight: 600; min-width: 0; overflow-wrap: anywhere; }
.priority-height { font-variant-numeric: tabular-nums; }
.priority-where { display: flex; flex-wrap: wrap; gap: 0.35rem; min-width: 0; }
.priority-on { justify-self: center; }
.kind { display: inline-block; font-size: 0.75rem; padding: 0.1rem 0.5rem; border-radius: 4px; white-space: nowrap; }
.kind-site { background: #cfe2ff; color: #084298; }
.kind-line { background: #e2d9f3; color: #432874; }
.place { display: inline-flex; align-items: center; gap: 0.35rem; font-size: 0.8rem; padding: 0.15rem 0.5rem; border-radius: 4px; background: var(--color-gray-200); border: 1px solid #dee2e6; white-space: nowrap; }
.place b { font-variant-numeric: tabular-nums; }
.place.zero { background: transparent; border-style: dashed; color: var(--text-muted); }
.none { font-size: 0.8rem; color: var(--text-muted); }
.is-off .priority-place, .is-off .priority-name, .is-off .priority-kind, .is-off .priority-height, .is-off .priority-where, .is-off .order-handle { opacity: 0.5; }
.priority-meta { display: contents; }
.height-card { display: none; }
@container (inline-size < 44rem) {
  .priority-head { display: none; }
  .priority-row {
    grid-template-columns: 2.75rem auto minmax(0, 1fr) auto;
    grid-template-areas: "handle place name on" ". . meta meta" ". . where where";
    row-gap: 0.35rem; justify-items: start;
  }
  .priority-meta { grid-area: meta; display: flex; flex-wrap: wrap; align-items: center; gap: 0.5rem; }
  .priority-row > .order-handle { grid-area: handle; }
  .priority-place { grid-area: place; }
  .priority-name { grid-area: name; }
  .priority-height { color: var(--color-gray-600); font-size: 0.85rem; }
  .priority-where { grid-area: where; }
  .priority-on { grid-area: on; }
  .height-cell { display: none; }
  .height-card { display: inline; }
}
</style>
