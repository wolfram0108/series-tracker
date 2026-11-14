<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue"
import SheetField from "../SheetField.vue"
import SheetRow from "../SheetRow.vue"
import StInput from "../StInput.vue"
import StBtn from "../StBtn.vue"
import StSelect from "../StSelect.vue"
import { api } from "../../api/client"
import type { components } from "../../api/schema"
import { useApi } from "../../composables/useApi"
import { useSSE } from "../../composables/useSSE"
import { t } from "../../i18n"
import { useSourcesStore, type SourcesSettingsView } from "../../stores/sources"

// The tab «Источники». The top row (like the groups of voice-overs) chooses a tracker; under it lie the mirrors of the
// chosen one, each change handed to the core at once. The tab ends with every domain the service reads pages of now.
// The screen shows what the core holds: the core announces every save, also one made in another window.
type Tracker = components["schemas"]["TrackerMirrors"]

const { request } = useApi()
const sourcesStore = useSourcesStore()
const view = ref<SourcesSettingsView | null>(null)
const selected = ref<number | null>(null)
const newMirror = ref("")
const busy = ref(false)

const trackers = computed(() => view.value?.trackers ?? [])
// Every domain the service reads pages of now, as its registries of trackers and sites give it: for reference only.
const readers = computed(() => view.value?.readers ?? [])
const chosen = computed(() => trackers.value.find((each) => each.id === selected.value) ?? null)
const mirrorsOf = (tracker: Tracker) => tracker.mirrors ?? []
const options = computed(() => [
  { label: t("settings.sources.select"), value: null as number | null },
  ...trackers.value.map((each) => ({ label: each.display_name, value: each.id as number | null })),
])

async function load() {
  const next = await request(api.GET("/api/sources/settings"))
  if (next) view.value = next
}

watch(() => sourcesStore.settings, (next) => {
  if (next) view.value = next
})
// A connection that came back may have missed a save.
watch(useSSE().connected, (up) => {
  if (up) void load()
})

function choose(id: number | null) {
  selected.value = id
  newMirror.value = ""
}

// What the core took comes back by its announcement; a refused change is read back, so the screen shows what it holds.
async function keep(tracker: Tracker, mirrors: string[]): Promise<boolean> {
  busy.value = true
  try {
    const ok = await request(
      api.PUT("/api/trackers/{tracker_id}", { params: { path: { tracker_id: tracker.id } }, body: { mirrors } } as never),
      { errorMessage: t("settings.sources.saveFailed") },
    )
    if (!ok) await load()
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
  if (mirrorsOf(tracker).includes(host) || (await keep(tracker, [...mirrorsOf(tracker), host]))) newMirror.value = ""
}

async function removeMirror(index: number) {
  const tracker = chosen.value
  if (tracker) await keep(tracker, mirrorsOf(tracker).filter((_, at) => at !== index))
}

onMounted(load)
</script>

<template>
  <div class="sheet settings-sources">
    <SheetRow class="sources-editor" :caption="t('settings.sources.trackers')">
      <SheetField :label="t('settings.sources.tracker')" icon="pi pi-wifi">
        <StSelect :model-value="selected" :options="options" :placeholder="t('settings.sources.select')" @update:model-value="choose($event as number | null)" />
      </SheetField>
      <template v-if="chosen">
        <div v-if="mirrorsOf(chosen).length" class="sheet-line">
          <span v-for="(mirror, index) in mirrorsOf(chosen)" :key="mirror" class="mirror-pill">
            {{ mirror }}
            <button class="mirror-pill-remove" :disabled="busy" :title="t('settings.sources.mirror.remove')" :aria-label="t('settings.sources.mirror.remove')" @click="removeMirror(index)" />
          </span>
        </div>
        <SheetField :label="t('settings.sources.mirror.new')" icon="pi pi-globe">
          <StInput v-model="newMirror" :placeholder="t('settings.sources.mirror.newHint')" @keyup.enter="addMirror" />
          <div class="constructor-item item-button-group">
            <StBtn icon="pi pi-plus" variant="add" :disabled="busy || !newMirror.trim()" :title="t('settings.sources.mirror.add')" :aria-label="t('settings.sources.mirror.add')" @click="addMirror" />
          </div>
        </SheetField>
      </template>
    </SheetRow>
    <SheetRow v-if="readers.length" :caption="t('settings.sources.domains.title')" class="parsed-domains">
      <div v-for="reader in readers" :key="`${reader.kind}:${reader.name}`" class="sheet-line domains-row">
        <span class="domains-name">{{ reader.name }}</span>
        <span class="domains-kind">{{ t(`settings.sources.domains.kind.${reader.kind === "tracker" ? "tracker" : "site"}`) }}</span>
        <span v-for="domain in reader.domains ?? []" :key="domain" class="mirror-pill">{{ domain }}</span>
      </div>
    </SheetRow>
  </div>
</template>

<style scoped>
.domains-row { padding-block: 0.4rem; }
.domains-row + .domains-row { border-top: 1px solid var(--border-color); }
.domains-name { font-weight: 600; }
.domains-kind { color: var(--text-muted); margin-inline-end: 0.25rem; }
</style>
