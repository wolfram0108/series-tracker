<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue"
import InfoTile from "../InfoTile.vue"
import SheetField from "../SheetField.vue"
import SheetRow from "../SheetRow.vue"
import StInput from "../StInput.vue"
import StNumber from "../StNumber.vue"
import StValue from "../StValue.vue"
import { api } from "../../api/client"
import type { components } from "../../api/schema"
import { useApi } from "../../composables/useApi"
import { useDownloadLimits } from "../../composables/useDownloadLimits"
import { useSSE } from "../../composables/useSSE"
import LoadGate from "../LoadGate.vue"
import { useLoad } from "../../composables/useLoad"
import { t, tn } from "../../i18n"
import { reasonText } from "../../lib/reason"
import { useProviderStore, type ProviderView } from "../../stores/provider"

// The tab «Чинес» of the extensions: the provider of external sources — its address and token, the state of the socket and what its
// manifest says, how many downloads of it go at once, the folder it writes to, and its applications. Without an address there is the
// line of the address and nothing else: the rest comes with the address, as it is typed. The address and the token are written when a
// field is left or Enter is pressed; the core takes them at once and tells the whole picture again, every open screen shows it.
type Problem = components["schemas"]["SettingProblem"]

const { request } = useApi()
const store = useProviderStore()
const limits = useDownloadLimits()

const url = ref("")
const token = ref("")
const problems = ref<Problem[]>([])
const atOnce = ref("")
// The address the core holds: what is typed follows the core while it was not changed by hand.
const held = ref("")

const view = computed(() => store.view)
const named = computed(() => url.value.trim() !== "")
const apps = computed(() => view.value?.apps ?? [])
const stale = computed(() => view.value?.state === "down" || view.value?.state === "incompatible")

function adopt(next: ProviderView): void {
  const was = held.value
  held.value = next.url
  if (url.value === was) url.value = next.url
}

async function load(): Promise<boolean> {
  const got = await request(api.GET("/api/provider"), { errorMessage: t("extensions.chines.loadFailed") })
  if (got) store.apply(got)
  return !!got
}
// The limit that goes with the address is read with it; the tab is put on show when both have come.
const gate = useLoad(async () => (await Promise.all([load(), limits.load()]))[0])

async function save(clearToken = false): Promise<void> {
  // The token this save carries; a token typed while it is on its way belongs to the next save, not to this one.
  const sent = clearToken ? "" : token.value
  if (url.value.trim() === held.value && sent === "" && !clearToken) return
  const reply = await request(
    api.PUT("/api/provider", { body: { url: url.value, ...(clearToken || sent !== "" ? { token: sent } : {}) } }),
    { errorMessage: t("extensions.chines.saveFailed") },
  )
  if (!reply) return
  problems.value = reply.problems
  if (reply.saved) {
    // What was typed is forgotten by the screen once the core holds it; a later typing stays.
    if (sent !== "" && token.value === sent) token.value = ""
    store.apply(reply.provider)
  }
}

// The limit of the downloads that go at once is shared with the debug tab (the other limits are its own).
async function saveAtOnce(): Promise<void> {
  await limits.save({ provider_at_once: atOnce.value.trim() })
}
watch(limits.limits, (got) => {
  if (got) atOnce.value = String(got.provider_at_once)
}, { immediate: true })

watch(view, (next) => {
  if (next) adopt(next)
}, { immediate: true })
// A connection that came back may have missed a change.
watch(useSSE().connected, (up) => {
  if (up) void gate.run()
})

const appNote = (app: { sources: number; lines: number }) =>
  t("extensions.chines.appNote", { sources: tn("extensions.chines.sources", app.sources), lines: tn("extensions.chines.lines", app.lines) })

onMounted(gate.run)
</script>

<template>
  <LoadGate :state="gate.state.value" :rows="3" @retry="gate.run">
    <div class="sheet" @change="save()" @keydown.enter="save()">
      <SheetRow :caption="t('extensions.sheet.connection')">
        <div v-if="named && view && view.state !== 'none'" class="pills">
          <template v-if="view.state === 'up'">
            <span class="pill pill-success"><i class="pi pi-check-circle" /> {{ t("extensions.chines.up") }}</span>
            <span v-if="view.provider" class="pill">{{ t("extensions.chines.provider", { provider: view.provider, version: view.version ?? "" }) }}</span>
            <span v-if="view.protocol != null" class="pill">{{ t("extensions.chines.protocol", { protocol: view.protocol }) }}</span>
            <span v-if="view.revision" class="pill font-mono">{{ t("extensions.chines.revision", { revision: view.revision }) }}</span>
          </template>
          <span v-else-if="view.state === 'connecting'" class="pill pill-info">{{ t("extensions.chines.connecting") }}</span>
          <span v-else class="pill pill-danger">{{ t("extensions.chines.unavailable", { error: reasonText(view.reason) }) }}</span>
        </div>
        <div class="sheet-pair">
          <SheetField :label="t('extensions.chines.address')" icon="pi pi-link" :state="problems.some((each) => each.field === 'url') ? 'invalid' : null">
            <StInput v-model="url" :placeholder="t('extensions.chines.addressExample')" />
          </SheetField>
          <SheetField v-if="named" :label="t('extensions.chines.token')" icon="pi pi-key">
            <StInput v-model="token" type="password" :placeholder="view?.has_token ? t('extensions.chines.tokenKept') : undefined" />
            <div v-if="view?.has_token && !token" class="constructor-item item-button-group">
              <button type="button" class="btn-icon btn-delete" :title="t('extensions.chines.tokenClear')"
                      :aria-label="t('extensions.chines.tokenClear')" @click="save(true)">
                <i class="pi pi-times" />
              </button>
            </div>
          </SheetField>
        </div>
        <p v-for="(problem, index) in problems" :key="index" class="sheet-problem">{{ reasonText(problem.detail) }}</p>
      </SheetRow>

      <template v-if="named">
        <SheetRow :caption="t('extensions.sheet.params')">
          <div class="sheet-pair">
            <SheetField :label="t('extensions.chines.atOnce')" :title="t('debug.downloads.providerAtOnceHint')" control>
              <StNumber v-model="atOnce" @change="saveAtOnce" />
            </SheetField>
          </div>
        </SheetRow>

        <SheetRow :caption="t('extensions.sheet.paths')">
          <div class="sheet-pair">
            <SheetField class="is-readonly" :label="t('extensions.chines.downloadsDir')" icon="pi pi-folder">
              <template #mark><span class="mark">{{ t("extensions.chines.fromConform") }}</span></template>
              <StValue :value="view?.downloads_dir ?? ''" mono />
            </SheetField>
          </div>
        </SheetRow>

        <SheetRow v-if="apps.length" :caption="t('extensions.sheet.apps')" :count="apps.length">
          <div class="tile-grid" :class="{ 'is-stale': stale }">
            <InfoTile v-for="app in apps" :key="app.name" :title="app.label" :note="appNote(app)" />
          </div>
        </SheetRow>
      </template>
    </div>
  </LoadGate>
</template>
