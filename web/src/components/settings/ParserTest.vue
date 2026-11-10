<script setup lang="ts">
import { ref, computed, watch } from "vue"
import Button from "primevue/button"
import StGroup from "../StGroup.vue"
import StIcon from "../StIcon.vue"
import StInput from "../StInput.vue"
import AppSearch from "../AppSearch.vue"
import { useRulesTest } from "../../composables/useRulesTest"
import { t } from "../../i18n"
import { reasonText } from "../../lib/reason"
import { cardState, excludingRule, pillsOf, resolutionBadge } from "../../lib/rulesTest"
import { progressText } from "../../lib/sources"

// The test of a profile: the rows of a page (a site, or a channel of VK) or the file names of a series, or titles
// typed in the field, each with the answer of the profile. The numbers in the answers are the constructor's alone.

// initialTitles: filled by the add window with the files of the release. initialUrl: the chosen web source, so that
// the page read is exactly that voice-over's.
const props = defineProps<{ profileId: number; initialTitles?: string[]; initialUrl?: string }>()
// The lines in the field as they stand: the quick templates of a rule «Таблица» are made of them.
const emit = defineEmits<{ lines: [lines: string[]] }>()

const test = useRulesTest(() => props.profileId)

const address = ref("")
// Where the rows come from: an address typed, or a card the search of a video application found.
const mode = ref<"address" | "apps">("address")
const query = ref("")
const titles = ref("")
watch(titles, (text) => emit("lines", text.split("\n").map((row) => row.trim()).filter((row) => row !== "")), { immediate: true })
const loadedNames = ref<number | null>(null)

if (props.initialTitles?.length) titles.value = props.initialTitles.join("\n")
if (props.initialUrl) address.value = props.initialUrl
watch(() => props.initialUrl, (v) => {
  if (v) address.value = v
})
watch(() => props.initialTitles, (v) => {
  if (v?.length) titles.value = v.join("\n")
})
// A page read to its end offers its rows in the field, to be edited and tried again.
watch(test.fetched, (rows) => {
  titles.value = rows.join("\n")
})

const dbId = computed(() => /^ID(\d+)$/i.exec(address.value.trim()))
const working = computed(() => test.state.value === "asking" || test.state.value === "reading")
const fetchIcon = computed(() => (working.value ? "pi-spin pi-spinner" : dbId.value ? "pi-database" : "pi-globe"))
const fetchLabel = computed(() => t(dbId.value ? "rulesTest.fetch.db" : "rulesTest.fetch.page"))
const failed = computed(() => test.state.value === "failed" || test.error.value !== "")
const failure = computed(() => test.error.value || reasonText(test.reason.value) || t("rulesTest.state.failed"))

async function fetchRows(): Promise<void> {
  loadedNames.value = null
  if (dbId.value) {
    const names = await test.loadFilenames(Number(dbId.value[1]))
    if (names) {
      titles.value = names.join("\n")
      loadedNames.value = names.length
    }
    return
  }
  await test.readPage(address.value, query.value)
}

// A card found is read as a page: its address stands in the field, so that the test can be asked again by it.
async function takeCard(url: string): Promise<void> {
  address.value = url
  query.value = ""
  mode.value = "address"
  await fetchRows()
}

function runTest(): Promise<void> {
  return test.runLines(titles.value.split("\n").map((row) => row.trim()).filter((row) => row !== ""))
}
</script>

<template>
  <div class="parser-test">
    <p class="parser-hint">{{ t("rulesTest.hint") }}</p>

    <div class="modern-fieldset">
      <div class="fieldset-content">
        <small class="fieldset-hint">
          <b>{{ t("rulesTest.channel") }}:</b> {{ t("rulesTest.channelHint") }}<br />
          <b>{{ t("rulesTest.sites") }}:</b> {{ t("rulesTest.sitesHint") }}
        </small>
        <div class="st-tabs st-tabs-wrap source-mode mt-3" role="tablist" :aria-label="t('sources.mode.label')">
          <button
            type="button" class="st-tab" role="tab" :class="{ active: mode === 'address' }" :aria-selected="mode === 'address'"
            @click="mode = 'address'"
          >
            <i class="pi pi-link tab-icon" /><span>{{ t("sources.mode.address") }}</span>
          </button>
          <button
            type="button" class="st-tab" role="tab" :class="{ active: mode === 'apps' }" :aria-selected="mode === 'apps'"
            @click="mode = 'apps'"
          >
            <i class="pi pi-search tab-icon" /><span>{{ t("sources.mode.apps") }}</span>
          </button>
        </div>
        <AppSearch
          v-if="mode === 'apps'" class="mt-3" :names="[]" :added="{}" :busy="working" :action="t('rulesTest.fetch.card')"
          @add="takeCard"
        />
        <div v-if="mode === 'address'" class="field-group mt-3">
          <StGroup>
            <StIcon :icon="dbId ? 'pi pi-database' : 'pi pi-globe'" />
            <StInput v-model="address" :label="t('rulesTest.field.address')" />
            <StIcon icon="pi pi-search" />
            <StInput v-model="query" :label="t('rulesTest.field.query')" />
            <div class="constructor-item item-button-group">
              <button
                class="btn-icon btn-search btn-text-icon"
                :disabled="!address.trim() || working"
                @click="fetchRows"
              >
                <i class="pi" :class="fetchIcon" />
                <span>{{ fetchLabel }}</span>
              </button>
            </div>
          </StGroup>
        </div>
        <small v-if="test.state.value === 'asking'" class="fieldset-hint">{{ t("rulesTest.state.asking") }}</small>
        <small v-else-if="test.state.value === 'reading'" class="fieldset-hint">
          {{ test.progress.value ? progressText(test.progress.value) : t("rulesTest.state.reading") }}
        </small>
        <small v-else-if="failed" class="fieldset-hint">{{ failure }}</small>
        <small v-else-if="loadedNames !== null" class="fieldset-hint">{{ t("rulesTest.names.loaded", { n: loadedNames }) }}</small>
      </div>
    </div>

    <StGroup class="group-auto-height">
      <textarea
        v-model="titles"
        class="parser-test-textarea"
        rows="8"
        :placeholder="t('rulesTest.placeholder')"
      />
    </StGroup>

    <div class="parser-test-actions">
      <Button
        :label="t('rulesTest.run')"
        :icon="test.state.value === 'asking' ? 'pi pi-spin pi-spinner' : 'pi pi-play'"
        severity="success"
        :disabled="!titles || working"
        @click="runTest"
      />
    </div>

    <div class="test-results-container">
      <div
        v-for="row in test.lines.value"
        :key="row.key"
        class="card-final card-test-result"
        :class="`status-${cardState(row.result)}`"
      >
        <div class="info-column">
          <div class="card-title-block">
            <span class="card-title" :title="row.line">{{ row.line }}</span>
            <div v-if="resolutionBadge(row.heights)" class="quality-badge">{{ resolutionBadge(row.heights) }}</div>
          </div>
        </div>
        <div class="pills-column">
          <div v-if="cardState(row.result) === 'no-match'" class="pill">
            <i class="pi pi-ban" /><span>{{ t("rulesTest.noMatch") }}</span>
          </div>
          <div v-else-if="cardState(row.result) === 'excluded'" class="pill">
            <i class="pi pi-times-circle" /><span>{{ t("rulesTest.excluded", { rule: excludingRule(row.result) }) }}</span>
          </div>
          <template v-else>
            <div v-for="pill in pillsOf(row.result)" :key="pill.key" class="pill">
              <i :class="pill.icon" /><span>{{ pill.label }}: <strong>{{ pill.value }}</strong></span>
            </div>
          </template>
        </div>
      </div>
    </div>
  </div>
</template>
