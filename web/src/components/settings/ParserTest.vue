<script setup lang="ts">
import { ref, computed, watch } from "vue"
import PickDropdown from "../PickDropdown.vue"
import Button from "primevue/button"
import StGroup from "../StGroup.vue"
import StIcon from "../StIcon.vue"
import StInput from "../StInput.vue"
import { useRulesTest } from "../../composables/useRulesTest"
import { t } from "../../i18n"
import { reasonText } from "../../lib/reason"
import { cardState, excludingRule, firedText, pillsOf, resolutionBadge } from "../../lib/rulesTest"
import { progressText, testOffers, type SourceAddress, type TestOffers } from "../../lib/sources"
import { stateLabel } from "../../lib/states"

// The test of a profile: the rows of a page (a site, or a channel of VK) or the file names of a series, or titles
// typed in the field, each with the answer of the profile. The numbers in the answers are the constructor's alone.

// initialTitles: filled by the add window with the files of the release. initialUrl: the chosen web source, so that
// the page read is exactly that voice-over's.
const props = defineProps<{
  profileId: number
  initialTitles?: string[]
  initialUrl?: string
  /** The sources of the series the test is for, as the series has them now; without it the fields offer nothing to paste. */
  loadSources?: () => Promise<SourceAddress[]>
}>()
// The lines in the field as they stand: the quick templates of a rule «Таблица» are made of them.
const emit = defineEmits<{ lines: [lines: string[]] }>()

const test = useRulesTest(() => props.profileId)
// The rows of the cards are the answers of the profile: another profile has none of them. What was typed stays.
watch(() => props.profileId, () => test.clear())

// What the fields offer to paste is asked when a list opens: a source added meanwhile is in it.
const offers = ref<TestOffers>({ addresses: [], queries: [] })
async function loadOffers(): Promise<void> {
  offers.value = testOffers(props.loadSources ? await props.loadSources() : [])
}
const addressItems = computed(() => offers.value.addresses.map((each) => ({ value: each.label, label: each.label })))
const queryItems = computed(() => offers.value.queries.map((each) => ({ value: each, label: each })))
function pickAddress(label: string): void {
  const found = offers.value.addresses.find((each) => each.label === label)
  if (!found) return
  address.value = found.address
  if (found.query !== null) query.value = found.query
}

const address = ref("")
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
const failure = computed(() => test.error.value || reasonText(test.reason.value) || t("reason.read_failed"))

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
        <div class="field-group mt-3">
          <StGroup>
            <StIcon :icon="dbId ? 'pi pi-database' : 'pi pi-globe'" />
            <StInput v-model="address" :label="t('rulesTest.field.address')" />
            <PickDropdown
              v-if="loadSources" :items="addressItems" :title="t('rulesTest.pick.address')" :empty="t('rulesTest.pick.addressEmpty')"
              :load="loadOffers" :min-width="420" @select="pickAddress"
            />
            <StIcon icon="pi pi-search" />
            <StInput v-model="query" :label="t('rulesTest.field.query')" />
            <PickDropdown
              v-if="loadSources" :items="queryItems" :title="t('rulesTest.pick.query')" :empty="t('rulesTest.pick.queryEmpty')"
              :load="loadOffers" :min-width="420" @select="query = $event"
            />
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
        <small v-if="test.state.value === 'asking'" class="fieldset-hint">{{ t("common.fetching") }}</small>
        <small v-else-if="test.state.value === 'reading'" class="fieldset-hint">
          {{ test.progress.value ? progressText(test.progress.value) : stateLabel("read") }}
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
          <small v-if="firedText(row.result)" class="fired-rules">{{ firedText(row.result) }}</small>
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

<style scoped>
/* The rules that gave the line something: said under the line, in the muted words of a hint. */
.fired-rules { color: var(--text-muted); font-size: 0.8rem; overflow-wrap: anywhere; }
</style>
