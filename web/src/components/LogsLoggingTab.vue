<script setup lang="ts">
import { ref, onMounted } from "vue"
import SheetField from "./SheetField.vue"
import SheetRow from "./SheetRow.vue"
import StSwitch from "./StSwitch.vue"
import { useToast } from "primevue/usetoast"
import { api } from "../api/client"
import { hasMessage, t, type MessageKey } from "../i18n"
import { useApi } from "../composables/useApi"

// "Setup" tab of the logs window: the detail of the logs per group of modules (the master switch of a group turns on
// the debug log of all its modules). Contract: GET /api/settings/debug_flags, POST {module, enabled}.

interface Mod { name: string; enabled: boolean }

const { request } = useApi()
const toast = useToast()
const loading = ref(true)
const groups = ref<Record<string, Mod[]>>({})
// The core names a section and a module by its code; the catalog words them, and a code it does not know shows as it came.
const sectionText = (code: string): string => hasMessage(`logs.section.${code}`) ? t(`logs.section.${code}` as MessageKey) : code
const moduleText = (name: string): string => hasMessage(`logs.module.${name}`) ? t(`logs.module.${name}` as MessageKey) : ""

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
  <div class="sheet logging-settings">
    <div v-if="loading" class="logging-loading"><i class="pi pi-spin pi-spinner" style="font-size: 1.6rem" /></div>
    <template v-else>
      <p class="sheet-hint">{{ t("logs.logging.hint") }}</p>
      <div class="logging-groups">
        <SheetRow v-for="(mods, g) in groups" :key="g" :caption="sectionText(String(g))">
          <SheetField :label="t('logs.logging.on')" control>
            <StSwitch :model-value="groupOn(mods)" @update:model-value="toggleGroup(g, mods, $event)" />
          </SheetField>
          <ul class="module-list">
            <li v-for="m in mods" :key="m.name">
              <strong>{{ m.name }}</strong><span v-if="moduleText(m.name)" class="muted"> — {{ moduleText(m.name) }}</span>
            </li>
          </ul>
        </SheetRow>
      </div>
    </template>
  </div>
</template>

<style scoped>
.logging-loading { display: flex; justify-content: center; padding: 2rem; color: var(--text-muted); }
.logging-groups { display: flex; flex-direction: column; }
.module-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.module-list li { font-size: 0.85rem; }
.module-list .muted { color: var(--text-muted); }
</style>
