<script setup lang="ts">
import { computed, onMounted, provide, ref, watch } from "vue"
import Button from "primevue/button"
import { api } from "../../api/client"
import type { components } from "../../api/schema"
import { useApi } from "../../composables/useApi"
import { useSSE } from "../../composables/useSSE"
import { t } from "../../i18n"
import { CHANGE_ANSWER, nextAnswer } from "../../lib/changeAnswer"
import { reasonText } from "../../lib/reason"
import { useVoicesStore } from "../../stores/voices"
import OrderList from "../OrderList.vue"
import VoiceGroupsEditor from "./VoiceGroupsEditor.vue"

// Settings of the voices: groups of voice-overs and the order of the sources. Every action goes to the core at once and
// the screen shows what the core holds, so an action the core refuses is shown with its reason and snaps back.
type View = components["schemas"]["VoiceSettingsView"]
type Problem = components["schemas"]["SettingProblem"]
type Groups = Record<string, string[]>

const { request } = useApi()
const voicesStore = useVoicesStore()

const view = ref<View | null>(null)
const problems = ref<Problem[]>([])
const busy = ref(false)
const answer = ref({ n: 0, ok: true })
provide(CHANGE_ANSWER, answer)

async function load() {
  const next = await request(api.GET("/api/voices/settings"), { errorMessage: t("voices.settings.loadFailed") })
  if (next) view.value = next
}

async function commit(change: { priority?: string[]; voice_groups?: Groups }): Promise<boolean> {
  if (!view.value) return false
  busy.value = true
  try {
    const reply = await request(
      api.PUT("/api/voices/settings", {
        body: { priority: view.value.priority, voice_groups: view.value.voice_groups, ...change },
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
  if (up) void load()
})

const problemsOf = (field: string) => problems.value.filter((problem) => problem.field === field)
const rejected = (field: string) => !!view.value?.rejected.includes(field)

const order = computed(() => view.value?.priority ?? [])

const keyOf = (name: string): string => name
function resetOrder() {
  if (view.value) void commit({ priority: [...view.value.default_priority] })
}

onMounted(load)
</script>

<template>
  <div class="settings-voices">
    <div class="modern-fieldset">
      <div class="fieldset-header"><i class="pi pi-users"></i> {{ t("voices.settings.voices.title") }}</div>
      <div class="fieldset-content">
        <p class="muted-hint">{{ t("voices.settings.voices.hint") }}</p>
        <VoiceGroupsEditor
          v-if="view"
          :groups="view.voice_groups"
          :busy="busy"
          :commit="(next: Groups) => commit({ voice_groups: next })"
          :problems="problemsOf('voice_groups')"
          :rejected="rejected('voice_groups')"
        />
      </div>
    </div>

    <div class="modern-fieldset">
      <div class="fieldset-header"><i class="pi pi-sort-amount-down"></i> {{ t("voices.settings.priority.title") }}</div>
      <div class="fieldset-content">
        <p class="muted-hint">{{ t("voices.settings.priority.hint") }}</p>
        <p v-if="rejected('priority')" class="problem">{{ t("voices.settings.rejected") }}</p>
        <OrderList class="priority-list" :items="order" :item-key="keyOf" :busy="busy" handle=".order-handle"
                   @order="commit({ priority: $event })">
          <template #item="{ item, index, step }">
            <li class="priority-row">
              <button type="button" class="order-handle" :disabled="busy" :aria-label="t('voices.settings.priority.drag')" @keydown="step">
                <i class="pi pi-bars" aria-hidden="true" />
              </button>
              <span class="priority-place">{{ index + 1 }}</span>
              <span class="priority-name">{{ item }}</span>
            </li>
          </template>
        </OrderList>
        <p v-for="(problem, index) in problemsOf('priority')" :key="index" class="problem">{{ reasonText(problem.detail) }}</p>
        <div class="actions-row">
          <Button :label="t('voices.settings.priority.reset')" icon="pi pi-undo" severity="secondary" size="small" text :disabled="busy" @click="resetOrder" />
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.settings-voices { display: flex; flex-direction: column; gap: 1.2rem; }
.muted-hint { color: var(--text-muted); margin: 0 0 0.75rem; }
.problem { color: var(--color-red-1); margin: 0.4rem 0; }
.actions-row { display: flex; flex-wrap: wrap; align-items: center; gap: 0.6rem; margin: 0.75rem 0 0; }
.priority-list { list-style: none; margin: 0 0 0.5rem; padding: 0; display: flex; flex-direction: column; gap: 0.35rem; }
.priority-row { display: flex; align-items: center; gap: 0.5rem; padding: 0.15rem 0.4rem; background: var(--bg-light); border-radius: var(--border-radius); }
.priority-place { color: var(--text-muted); min-width: 1.5rem; }
.priority-name { flex: 1 1 auto; font-weight: 600; }
</style>
