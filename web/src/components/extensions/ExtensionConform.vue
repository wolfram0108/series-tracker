<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue"
import InputText from "primevue/inputtext"
import SelectButton from "primevue/selectbutton"
import ToggleSwitch from "primevue/toggleswitch"
import SheetRow from "../SheetRow.vue"
import StControl from "../StControl.vue"
import StGroup from "../StGroup.vue"
import StInput from "../StInput.vue"
import StLabel from "../StLabel.vue"
import { api } from "../../api/client"
import type { components } from "../../api/schema"
import { useApi } from "../../composables/useApi"
import { useSSE } from "../../composables/useSSE"
import { t } from "../../i18n"
import { reasonText } from "../../lib/reason"
import { useConformStore } from "../../stores/conform"
import { useQueuesStore } from "../../stores/queues"

// The tab «Conform» of the extensions: the alignment server's address, the work folder as this server and as the daemon name it, and
// how the daemon is asked to align. The address and the folders are written when a field is left or Enter is pressed, the parameters
// at once; the core holds both, and a refused value stays on screen with its reasons. Without an address there is the line of the
// address and nothing else: the rest comes with the address.
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

// What each text field last took from the core, or last handed to it. A field that differs from it is being edited: what the core
// says next (the answer to an earlier save, an announcement from another window) never takes a typed value away.
type Field = "url" | "here" | "there" | "drift"
const fields = { url, here, there, drift }
const taken: Record<Field, string> = { url: "", here: "", there: "", drift: "" }

function take(name: Field, value: string) {
  if (fields[name].value === taken[name]) fields[name].value = value
  taken[name] = value
}

function adopt(view: View) {
  // The announcement of an earlier save may come after the answer to a later one: an older view is not taken.
  if (core.value && (view.revision ?? 0) < (core.value.revision ?? 0)) return
  core.value = view
  take("url", view.url)
  take("here", view.work_dir)
  take("there", view.work_dir_remote)
  if (pending) return
  local.value = view.params ? { ...view.params } : null
  const shown = view.params?.drift_speed_pct ?? view.domain?.drift_default
  take("drift", shown == null ? "" : String(shown))
}

async function load() {
  const view = await request(api.GET("/api/conform/settings"), { errorMessage: t("conform.settings.loadFailed") })
  if (view) adopt(view)
}

const dirty = computed(() => !!core.value && (url.value !== core.value.url || here.value !== core.value.work_dir
  || there.value !== core.value.work_dir_remote))

async function save(): Promise<void> {
  if (!dirty.value) return
  const sent = { url: url.value, here: here.value, there: there.value }
  const reply = await request(
    api.PUT("/api/conform/settings", { body: { url: sent.url, work_dir: sent.here, work_dir_remote: sent.there } }),
    { errorMessage: t("conform.settings.saveFailed") },
  )
  if (!reply) return
  problems.value = reply.problems
  if (reply.saved) {
    // What was written is the core's now: the fields still showing it take what the core holds, a field typed meanwhile keeps its text.
    Object.assign(taken, sent)
    adopt(reply.settings)
  }
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
  // Left or entered: the field is no longer an edit, the core's answer (or its refusal) shows in it.
  taken.drift = drift.value
  const value = Number(drift.value.replace(",", "."))
  if (drift.value.trim() === "" || !Number.isFinite(value)) {
    if (core.value) adopt(core.value)
    return
  }
  void setParams({ drift_speed_pct: value })
}

// The pipeline flag that finished jobs leave the queue by themselves: the core reads it with the settings, writes it by its own call.
async function setClear(enabled: boolean): Promise<void> {
  const done = await request(api.POST("/api/settings/conform_clear_finished" as never, { body: { enabled } } as never),
    { errorMessage: t("debug.flag.saveFailed") })
  if (done !== null) await load()
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
// The rest of the tab comes with the address, as it is typed.
const named = computed(() => url.value.trim() !== "")

onMounted(load)
</script>

<template>
  <div class="sheet" @change="save" @keydown.enter="save">
    <SheetRow :caption="t('extensions.sheet.connection')">
      <div v-if="named && core?.url" class="pills">
        <template v-if="daemon?.up">
          <span class="pill pill-success"><i class="pi pi-check-circle" /> {{ t("agents.conform.daemon.up") }}</span>
          <span v-if="daemon.version" class="pill">{{ t("agents.conform.daemon.version", { version: daemon.version }) }}</span>
          <span v-if="daemon.gpu_name || daemon.device" class="pill">{{ daemon.gpu_name || daemon.device }}</span>
        </template>
        <span v-else-if="daemon?.error" class="pill pill-danger">{{ t("agents.conform.daemon.down", { error: reasonText(daemon.error) }) }}</span>
        <span v-else class="pill pill-info">{{ t("conform.settings.connecting") }}</span>
      </div>
      <div class="sheet-pair">
        <StGroup>
          <StLabel icon="pi pi-link" :text="t('conform.settings.url')" />
          <StInput v-model="url" :aria-label="t('conform.settings.url')" />
        </StGroup>
      </div>
    </SheetRow>

    <template v-if="named">
      <SheetRow :caption="t('extensions.sheet.paths')">
        <div class="sheet-pair">
          <StGroup>
            <StLabel icon="pi pi-folder" :text="t('conform.settings.local')" />
            <StInput v-model="here" :aria-label="t('conform.settings.local')" />
          </StGroup>
          <StGroup>
            <StLabel icon="pi pi-arrow-right" :text="t('conform.settings.daemon')" />
            <StInput v-model="there" :aria-label="t('conform.settings.daemon')" />
          </StGroup>
        </div>
      </SheetRow>

      <SheetRow v-if="params" :caption="t('extensions.sheet.params')">
        <div class="sheet-pair">
          <StGroup :title="t('conform.params.methodHint')">
            <StLabel :text="t('conform.params.method')" />
            <StControl>
              <SelectButton :model-value="params.audio_method" :options="methods" option-label="label" option-value="value"
                            :allow-empty="false" :aria-label="t('conform.params.method')"
                            @update:model-value="setParams({ audio_method: $event })" />
            </StControl>
          </StGroup>
          <StGroup :title="t('conform.params.driftHint', { value: domain?.drift_default ?? '' })">
            <StLabel :text="t('conform.params.drift')" />
            <StControl>
              <span v-if="domain" class="item-hint">{{ t("conform.params.driftRange", { low: domain.drift_min, high: domain.drift_max }) }}</span>
              <InputText v-model="drift" inputmode="decimal" class="conform-num" :aria-label="t('conform.params.drift')" @change="setDrift" />
            </StControl>
          </StGroup>
          <StGroup :title="t('conform.params.keepTmpHint')">
            <StLabel :text="t('conform.params.keepTmp')" />
            <StControl>
              <ToggleSwitch :model-value="params.keep_tmp" :aria-label="t('conform.params.keepTmp')" @update:model-value="setParams({ keep_tmp: $event })" />
            </StControl>
          </StGroup>
          <StGroup :title="t('conform.params.fillHint')">
            <StLabel :text="t('conform.params.fill')" />
            <StControl>
              <ToggleSwitch :model-value="params.fill_silence" :aria-label="t('conform.params.fill')" @update:model-value="setParams({ fill_silence: $event })" />
            </StControl>
          </StGroup>
          <StGroup class="is-wide" :title="t('conform.params.edgesHint')">
            <StLabel :text="t('extensions.conform.edges')" />
            <StControl>
              <SelectButton :model-value="params.extend_edges" :options="edges" option-label="label" option-value="value"
                            :allow-empty="false" :aria-label="t('extensions.conform.edges')"
                            @update:model-value="setParams({ extend_edges: $event })" />
            </StControl>
          </StGroup>
          <StGroup class="is-wide" :title="t('conform.settings.clearFinishedHint')">
            <StLabel :text="t('conform.settings.clearFinished')" />
            <StControl>
              <ToggleSwitch :model-value="core?.clear_finished ?? false" :aria-label="t('conform.settings.clearFinished')" @update:model-value="setClear($event)" />
            </StControl>
          </StGroup>
        </div>
      </SheetRow>
    </template>

    <p v-for="(problem, index) in problems" :key="index" class="sheet-problem">{{ reasonText(problem.detail) }}</p>
  </div>
</template>

<style scoped>
.conform-num { inline-size: 5rem; }
</style>
