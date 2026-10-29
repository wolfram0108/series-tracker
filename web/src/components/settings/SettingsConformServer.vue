<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue"
import StGroup from "../StGroup.vue"
import StIcon from "../StIcon.vue"
import StInput from "../StInput.vue"
import { api } from "../../api/client"
import type { components } from "../../api/schema"
import { useApi } from "../../composables/useApi"
import { t } from "../../i18n"
import { reasonText } from "../../lib/reason"
import { useConformStore } from "../../stores/conform"
import { useQueuesStore } from "../../stores/queues"

// The alignment server: the daemon's address and the work folder as this server and as the daemon name it.
// The save button of the tab calls save(): it writes both when the draft differs from what the core holds and the core
// takes the address at once; a refused draft stays on screen with its reasons.
type View = components["schemas"]["ConformSettingsView"]
type Problem = components["schemas"]["SettingProblem"]

const { request } = useApi()
const store = useConformStore()
const queues = useQueuesStore()

const core = ref<View | null>(null)
const url = ref("")
const here = ref("")
const there = ref("")
const problems = ref<Problem[]>([])

function adopt(view: View) {
  core.value = view
  url.value = view.url
  here.value = view.work_dir
  there.value = view.work_dir_remote
}

async function load() {
  const view = await request(api.GET("/api/conform/settings"), { errorMessage: t("conform.settings.loadFailed") })
  if (view) adopt(view)
}

const dirty = computed(() => !!core.value && (url.value !== core.value.url || here.value !== core.value.work_dir
  || there.value !== core.value.work_dir_remote))

/** True when there was nothing to write or the core took it. */
async function save(): Promise<boolean> {
  if (!dirty.value) return true
  const reply = await request(
    api.PUT("/api/conform/settings", { body: { url: url.value, work_dir: here.value, work_dir_remote: there.value } }),
    { errorMessage: t("conform.settings.saveFailed") },
  )
  if (!reply) return false
  problems.value = reply.problems
  if (reply.saved) adopt(reply.settings)
  return reply.saved
}

// The core announces every save, also one made in another window.
watch(() => store.settings, (next) => {
  if (next) adopt(next)
})

const daemon = computed(() => queues.conform?.daemon)

onMounted(load)
defineExpose({ save })
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

      <p v-for="(problem, index) in problems" :key="index" class="problem">{{ reasonText(problem.detail) }}</p>
    </div>
  </div>
</template>

<style scoped>
.conform-server { display: flex; flex-direction: column; gap: 0.9rem; }
.problem { color: var(--color-red-1); margin: 0; }
.conform-status { flex-wrap: wrap; }
.conform-status .pill { min-width: 0; max-width: 100%; overflow: hidden; text-overflow: ellipsis; }
/* A row of the form on a narrow screen: its parts wrap into lines instead of running out of the window. */
.conform-server :deep(.input-constructor-group) { height: auto; flex-wrap: wrap; }
.conform-server :deep(.input-constructor-group > .constructor-item) { min-height: 62px; }
.conform-server :deep(.item-floating-label) { min-width: 0; flex: 1 1 8rem; }
</style>
