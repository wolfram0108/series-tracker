<script setup lang="ts">
import { ref, onMounted } from "vue"
import ToggleSwitch from "primevue/toggleswitch"
import { useToast } from "primevue/usetoast"
import { api } from "../api/client"
import { t } from "../i18n"
import { useApi } from "../composables/useApi"

// "Setup" tab of the logs window: the detail of the logs per group of modules (the master switch of a group turns on
// the debug log of all its modules). Contract: GET /api/settings/debug_flags, POST {module, enabled}.

interface Mod { name: string; description: string; enabled: boolean }

const { request } = useApi()
const toast = useToast()
const loading = ref(true)
const groups = ref<Record<string, Mod[]>>({})

async function load() {
  loading.value = true
  const d = (await request(api.GET("/api/settings/debug_flags"), {
    errorMessage: t("logs.logging.loadFailed"),
  })) as { logging_modules?: Record<string, Mod[]> } | null
  loading.value = false
  if (d) {
    groups.value = d.logging_modules ?? {}
  }
}

function groupOn(mods: Mod[]): boolean {
  return mods.length > 0 && mods.every((m) => m.enabled)
}

async function postFlag(name: string, enabled: boolean): Promise<boolean> {
  const ok = await request(
    api.POST("/api/settings/debug_flags", { body: { module: name, enabled } } as never),
    { errorMessage: t("logs.logging.saveFailed", { name }) },
  )
  return ok !== null
}

async function toggleGroup(g: string, mods: Mod[], value: boolean) {
  mods.forEach((m) => (m.enabled = value)) // оптимистично
  const results = await Promise.all(mods.map((m) => postFlag(m.name, value)))
  if (results.every(Boolean)) {
    toast.add({ severity: "info", summary: g, detail: value ? t("logs.logging.enabled") : t("logs.logging.disabled"), life: 2000 })
  } else {
    await load() // откат к серверному состоянию
  }
}

onMounted(load)
</script>

<template>
  <div class="logging-settings">
    <div v-if="loading" class="logging-loading"><i class="pi pi-spin pi-spinner" style="font-size: 1.6rem" /></div>
    <template v-else>
      <p class="muted-hint">
        {{ t("logs.logging.hint") }}
      </p>
      <div class="logging-groups">
        <div v-for="(mods, g) in groups" :key="g" class="modern-fieldset">
          <div class="fieldset-header logging-group-header">
            <span class="fieldset-title">{{ g }}</span>
            <ToggleSwitch :model-value="groupOn(mods)" @update:model-value="toggleGroup(g, mods, $event)" />
          </div>
          <div class="fieldset-content">
            <ul class="module-list">
              <li v-for="m in mods" :key="m.name">
                <strong>{{ m.name }}</strong><span class="muted"> — {{ m.description }}</span>
              </li>
            </ul>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.logging-settings { display: flex; flex-direction: column; gap: 16px; }
.logging-loading { display: flex; justify-content: center; padding: 2rem; color: var(--text-muted); }
.muted-hint { color: var(--text-muted); font-size: 0.88rem; margin: 0 0 8px; }
.logging-groups { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.logging-group-header { display: flex; align-items: center; justify-content: space-between; }
.module-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.module-list li { font-size: 0.85rem; }
.module-list .muted { color: var(--text-muted); }
@media (max-width: 900px) {
  .logging-groups { grid-template-columns: 1fr; }
}
</style>
