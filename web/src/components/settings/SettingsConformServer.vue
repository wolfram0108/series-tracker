<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue"
import InputText from "primevue/inputtext"
import SelectButton from "primevue/selectbutton"
import ToggleSwitch from "primevue/toggleswitch"
import StGroup from "../StGroup.vue"
import StIcon from "../StIcon.vue"
import StInput from "../StInput.vue"
import { api } from "../../api/client"
import type { components } from "../../api/schema"
import { useApi } from "../../composables/useApi"
import { useSSE } from "../../composables/useSSE"
import { t } from "../../i18n"
import { reasonText } from "../../lib/reason"
import { useConformStore } from "../../stores/conform"
import { useQueuesStore } from "../../stores/queues"

// The alignment server: the daemon's address and the work folder as this server and as the daemon name it, and how the
// daemon is asked to align. The address and the folders are written when a field is left or Enter is pressed, the
// parameters at once; the core holds both, and a refused value stays on screen with its reasons.
type View = components["schemas"]["ConformSettingsView"]
type Params = components["schemas"]["ConformParams"]
type Problem = components["schemas"]["SettingProblem"]

const { request } = useApi()
const store = useConformStore()
const queues = useQueuesStore()

const core = ref<View | null>(null)
const url = ref("")
const here = ref("")
const there = ref("")
const drift = ref("")
const problems = ref<Problem[]>([])
// The parameters as the screen shows them: a change shows at once and its saves go one after another, each with the
// latest of them, so that an answer to an earlier save never takes back a later change.
const local = ref<Params | null>(null)
let pending = 0
let saves: Promise<void> = Promise.resolve()

function adopt(view: View) {
  // The announcement of an earlier save may come after the answer to a later one: an older view is not taken.
  if (core.value && (view.revision ?? 0) < (core.value.revision ?? 0)) return
  core.value = view
  url.value = view.url
  here.value = view.work_dir
  there.value = view.work_dir_remote
  if (pending) return
  local.value = view.params ? { ...view.params } : null
  const shown = view.params?.drift_speed_pct ?? view.domain?.drift_default
  drift.value = shown == null ? "" : String(shown)
}

async function load() {
  const view = await request(api.GET("/api/conform/settings"), { errorMessage: t("conform.settings.loadFailed") })
  if (view) adopt(view)
}

const dirty = computed(() => !!core.value && (url.value !== core.value.url || here.value !== core.value.work_dir
  || there.value !== core.value.work_dir_remote))

async function save(): Promise<void> {
  if (!dirty.value) return
  const reply = await request(
    api.PUT("/api/conform/settings", { body: { url: url.value, work_dir: here.value, work_dir_remote: there.value } }),
    { errorMessage: t("conform.settings.saveFailed") },
  )
  if (!reply) return
  problems.value = reply.problems
  if (reply.saved) adopt(reply.settings)
}

function setParams(change: Partial<Params>): Promise<void> {
  if (!local.value) return saves
  local.value = { ...local.value, ...change }
  pending += 1
  saves = saves.then(async () => {
    try {
      const reply = await request(
        api.PUT("/api/conform/params", { body: { ...local.value! } }),
        { errorMessage: t("conform.params.saveFailed") },
      )
      if (!reply) return
      problems.value = reply.problems
      if ((reply.settings.revision ?? 0) >= (core.value?.revision ?? 0)) core.value = reply.settings
      // A refused change goes back to what the core holds.
      if (!reply.saved) local.value = { ...reply.settings.params }
    } finally {
      pending -= 1
      if (!pending && core.value) adopt(core.value)
    }
  })
  return saves
}

function setDrift() {
  const value = Number(drift.value.replace(",", "."))
  if (drift.value.trim() === "" || !Number.isFinite(value)) {
    if (core.value) adopt(core.value)
    return
  }
  void setParams({ drift_speed_pct: value })
}

// The core announces every save, also one made in another window, and what the daemon accepts when it connects; a
// connection that came back may have missed one.
watch(() => store.settings, (next) => {
  if (next) adopt(next)
})
watch(useSSE().connected, (up) => {
  if (up) void load()
})

const daemon = computed(() => queues.conform?.daemon)
const params = computed(() => local.value)
const domain = computed(() => core.value?.domain ?? null)
// The names of the methods the daemon knows now; a method it names that is not here is shown by its own name.
const METHOD_LABELS: Record<string, string> = { band: t("conform.params.method.band"), muq: t("conform.params.method.muq") }
const methods = computed(() => (domain.value?.audio_methods ?? (params.value ? [params.value.audio_method] : []))
  .map((value) => ({ value, label: METHOD_LABELS[value] ?? value })))
const edges = computed(() => [
  { value: true, label: t("conform.params.edges.extend") },
  { value: false, label: t("conform.params.edges.cut") },
])

onMounted(load)
</script>

<template>
  <div class="modern-fieldset">
    <div class="fieldset-header"><i class="pi pi-sliders-h"></i> {{ t("conform.settings.title") }}</div>
    <div class="fieldset-content conform-server">
      <div class="queue-row conform-status">
        <div v-if="!core?.url" class="pill pill-secondary">{{ t("agents.conform.daemon.idle") }}</div>
        <template v-else-if="daemon?.up">
          <div class="pill pill-success"><i class="pi pi-check-circle"></i> {{ t("agents.conform.daemon.up") }}</div>
          <div v-if="daemon.version" class="pill">{{ t("agents.conform.daemon.version", { version: daemon.version }) }}</div>
          <div v-if="daemon.gpu_name || daemon.device" class="pill">{{ daemon.gpu_name || daemon.device }}</div>
        </template>
        <div v-else-if="daemon?.error" class="pill pill-danger">{{ t("agents.conform.daemon.down", { error: reasonText(daemon.error) }) }}</div>
        <div v-else class="pill pill-info">{{ t("conform.settings.connecting") }}</div>
      </div>

      <div class="conform-place" @change="save" @keydown.enter="save">
        <StGroup>
          <StIcon icon="pi pi-link" />
          <StInput v-model="url" :label="t('conform.settings.url')" />
        </StGroup>

        <StGroup>
          <StIcon icon="pi pi-folder" />
          <StInput v-model="here" :label="t('conform.settings.local')" />
          <StIcon icon="pi pi-arrow-right" />
          <StInput v-model="there" :label="t('conform.settings.daemon')" />
        </StGroup>
      </div>

      <template v-if="params">
        <div class="conform-sub">{{ t("conform.params.episode") }}</div>
        <div class="conform-row">
          <div class="conform-control" :title="t('conform.params.methodHint')">
            <span class="conform-label">{{ t("conform.params.method") }}</span>
            <SelectButton :model-value="params.audio_method" :options="methods" option-label="label" option-value="value"
                          :allow-empty="false" :aria-label="t('conform.params.method')"
                          @update:model-value="setParams({ audio_method: $event })" />
          </div>
          <div class="conform-control" :title="t('conform.params.driftHint', { value: domain?.drift_default ?? '' })">
            <span class="conform-label">{{ t("conform.params.drift") }}</span>
            <div class="conform-drift">
              <InputText v-model="drift" inputmode="decimal" class="conform-num" :aria-label="t('conform.params.drift')"
                         @change="setDrift" />
              <span v-if="domain" class="conform-range">{{ t("conform.params.driftRange", { low: domain.drift_min, high: domain.drift_max }) }}</span>
            </div>
          </div>
        </div>
        <div class="conform-switches">
          <label class="conform-switch" :title="t('conform.params.keepTmpHint')">
            <ToggleSwitch :model-value="params.keep_tmp" @update:model-value="setParams({ keep_tmp: $event })" />
            <span>{{ t("conform.params.keepTmp") }}</span>
          </label>
          <label class="conform-switch" :title="t('conform.params.fillHint')">
            <ToggleSwitch :model-value="params.fill_silence" @update:model-value="setParams({ fill_silence: $event })" />
            <span>{{ t("conform.params.fill") }}</span>
          </label>
        </div>

        <div class="conform-sub">{{ t("conform.params.compilation") }}</div>
        <div class="conform-control" :title="t('conform.params.edgesHint')">
          <span class="conform-label">{{ t("conform.params.edges") }}</span>
          <SelectButton :model-value="params.extend_edges" :options="edges" option-label="label" option-value="value"
                        :allow-empty="false" :aria-label="t('conform.params.edges')" class="conform-edges"
                        @update:model-value="setParams({ extend_edges: $event })" />
        </div>
      </template>

      <p v-for="(problem, index) in problems" :key="index" class="problem">{{ reasonText(problem.detail) }}</p>
    </div>
  </div>
</template>

<style scoped>
.conform-server { display: flex; flex-direction: column; gap: 0.9rem; }
.conform-place { display: flex; flex-direction: column; gap: 0.9rem; }
.problem { color: var(--color-red-1); margin: 0; }
.conform-status { flex-wrap: wrap; }
.conform-status .pill { min-width: 0; max-width: 100%; overflow: hidden; text-overflow: ellipsis; }
/* A row of the form on a narrow screen: its parts wrap into lines instead of running out of the window. */
.conform-server :deep(.input-constructor-group) { height: auto; flex-wrap: wrap; }
.conform-server :deep(.input-constructor-group > .constructor-item) { min-height: 62px; }
.conform-server :deep(.item-floating-label) { min-width: 0; flex: 1 1 8rem; }
.conform-sub { display: flex; align-items: center; gap: 0.6rem; font-size: 0.8rem; font-weight: 600; letter-spacing: 0.04em;
  text-transform: uppercase; color: var(--color-gray-600); margin-top: 0.3rem; }
.conform-sub::after { content: ""; flex: 1; height: 1px; background: var(--border-color); }
.conform-row { display: flex; flex-wrap: wrap; gap: 1rem 1.75rem; align-items: flex-start; }
.conform-control { display: flex; flex-direction: column; gap: 0.4rem; min-width: 0; }
.conform-label { color: var(--text-muted); font-size: 0.85rem; }
.conform-drift { display: flex; align-items: center; gap: 0.5rem; }
.conform-range { color: var(--text-muted); font-size: 0.85rem; }
.conform-switches { display: flex; flex-wrap: wrap; gap: 0.75rem 1.75rem; }
.conform-switch { display: flex; align-items: center; gap: 0.6rem; cursor: pointer; }
.conform-num { width: 6rem; }
.conform-edges { display: flex; flex-wrap: wrap; }
.conform-edges :deep(.p-togglebutton) { flex: 1 1 auto; }
</style>
