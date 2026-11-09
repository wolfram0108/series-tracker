<script setup lang="ts">
import { computed, onMounted, ref } from "vue"
import StGroup from "../StGroup.vue"
import StIcon from "../StIcon.vue"
import StInput from "../StInput.vue"
import StBtn from "../StBtn.vue"
import StSelect from "../StSelect.vue"
import { api } from "../../api/client"
import type { components } from "../../api/schema"
import { useApi } from "../../composables/useApi"
import { t } from "../../i18n"

// The tab «Источники». The top row (like the groups of voice-overs) chooses a tracker; under it lie the mirrors of the
// chosen one, each change handed to the core at once. The tab ends with every domain the service reads pages of now.
interface Tracker { id: number; display_name: string; mirrors: string[] }

const { request } = useApi()
const trackers = ref<Tracker[]>([])
const selected = ref<number | null>(null)
const newMirror = ref("")
const busy = ref(false)
// Every domain the service reads pages of now, as its registries of trackers and sites give it: for reference only.
const readers = ref<components["schemas"]["ParsedDomains"][]>([])

const chosen = computed(() => trackers.value.find((each) => each.id === selected.value) ?? null)
const options = computed(() => [
  { label: t("settings.sources.select"), value: null as number | null },
  ...trackers.value.map((each) => ({ label: each.display_name, value: each.id as number | null })),
])

async function loadDomains() {
  const data = await request(api.GET("/api/sources/domains"))
  if (data) readers.value = data.readers ?? []
}

async function load() {
  const data = (await request(api.GET("/api/trackers"))) as unknown
  if (!Array.isArray(data)) return
  trackers.value = (data as Array<{ id: number; display_name: string; mirrors?: string[] }>).map((each) => ({
    id: each.id, display_name: each.display_name, mirrors: each.mirrors ?? [],
  }))
}

function choose(id: number | null) {
  selected.value = id
  newMirror.value = ""
}

// The core keeps what it took; a refused change is read back, so the screen shows what the core holds.
async function keep(tracker: Tracker, mirrors: string[]): Promise<boolean> {
  busy.value = true
  try {
    const ok = await request(
      api.PUT("/api/trackers/{tracker_id}", { params: { path: { tracker_id: tracker.id } }, body: { mirrors } } as never),
      { errorMessage: t("settings.sources.saveFailed") },
    )
    if (ok) tracker.mirrors = mirrors
    else await load()
    void loadDomains()
    return !!ok
  } finally {
    busy.value = false
  }
}

function hostOf(text: string): string | null {
  try {
    return new URL("https://" + text.replace(/^(https?:\/\/)?/, "")).hostname
  } catch {
    return null
  }
}

async function addMirror() {
  const tracker = chosen.value
  const host = hostOf(newMirror.value.trim())
  if (!tracker || !host) return
  if (tracker.mirrors.includes(host) || (await keep(tracker, [...tracker.mirrors, host]))) newMirror.value = ""
}

async function removeMirror(index: number) {
  const tracker = chosen.value
  if (tracker) await keep(tracker, tracker.mirrors.filter((_, at) => at !== index))
}

onMounted(() => {
  void load()
  void loadDomains()
})
</script>

<template>
  <div class="settings-sources">
    <div class="modern-fieldset">
      <div class="fieldset-header"><i class="pi pi-wifi"></i> {{ t("settings.sources.trackers") }}</div>
      <div class="fieldset-content sources-editor">
        <StGroup>
          <StIcon icon="pi pi-wifi" />
          <StSelect :model-value="selected" :options="options" :placeholder="t('settings.sources.select')" @update:model-value="choose($event as number | null)" />
        </StGroup>
        <template v-if="chosen">
          <div v-if="chosen.mirrors.length" class="mirrors-row">
            <span v-for="(mirror, index) in chosen.mirrors" :key="mirror" class="mirror-pill">
              {{ mirror }}
              <button class="mirror-pill-remove" :disabled="busy" :title="t('settings.sources.mirror.remove')" :aria-label="t('settings.sources.mirror.remove')" @click="removeMirror(index)" />
            </span>
          </div>
          <StGroup>
            <StInput v-model="newMirror" :label="t('settings.sources.mirror.new')" @keyup.enter="addMirror" />
            <div class="constructor-item item-button-group">
              <StBtn icon="pi pi-plus" variant="add" :disabled="busy || !newMirror.trim()" :title="t('settings.sources.mirror.add')" :aria-label="t('settings.sources.mirror.add')" @click="addMirror" />
            </div>
          </StGroup>
        </template>
      </div>
    </div>
    <div v-if="readers.length" class="modern-fieldset parsed-domains">
      <div class="fieldset-header"><i class="pi pi-globe"></i> {{ t("settings.sources.domains.title") }}</div>
      <div class="fieldset-content">
        <div v-for="reader in readers" :key="`${reader.kind}:${reader.name}`" class="domains-row">
          <span class="domains-name">{{ reader.name }}</span>
          <span class="domains-kind">{{ t(`settings.sources.domains.kind.${reader.kind === "tracker" ? "tracker" : "site"}`) }}</span>
          <span v-for="domain in reader.domains ?? []" :key="domain" class="mirror-pill">{{ domain }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.sources-editor { display: flex; flex-direction: column; gap: 0.9rem; }
.mirrors-row { display: flex; flex-wrap: wrap; gap: 8px; }
.domains-row { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; padding: 6px 0; }
.domains-row + .domains-row { border-top: 1px solid var(--border-color); }
.domains-name { font-weight: 600; }
.domains-kind { color: var(--text-muted); margin-right: 4px; }
</style>
