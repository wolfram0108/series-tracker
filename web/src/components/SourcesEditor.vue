<script setup lang="ts">
import { computed, ref, watch } from "vue"
import AppSearch from "./AppSearch.vue"
import SourceCard from "./SourceCard.vue"
import StGroup from "./StGroup.vue"
import StIcon from "./StIcon.vue"
import StInput from "./StInput.vue"
import { t } from "../i18n"
import { joinVkAddress, splitVkAddress, vkQuery, type SourceItem } from "../lib/sources"
import { kindOf } from "../composables/sourceAddress"
import type { DraftPatch } from "../composables/useSourceDrafts"

// The sources of a series, one group per source. The add window and the properties of a series use this one
// component; they differ in who keeps the list (a draft until "Add", the core at once), not in what is drawn.
const props = withDefaults(defineProps<{
  items: SourceItem[]
  profiles: { id: number; name: string }[]
  /** The words of the last refusal of an action. */
  error?: string
  busy?: boolean
  /** A torrent series has one source: no source is added to it or removed from it. */
  editable?: boolean
  /** Takes the typed address and says whether a source was made of it; the field is cleared only then. */
  adder: (address: string) => Promise<boolean>
  /** The names of the series the search of the video applications offers (its own, the original one of TMDB). */
  names?: { text: string; mark?: string }[]
  /** Names that cost a request (the original name of TMDB): asked once, when the search is first opened. */
  loadNames?: () => Promise<{ text: string; mark?: string }[]>
}>(), { error: "", busy: false, editable: true, names: () => [], loadNames: undefined })

const emit = defineEmits<{
  (e: "remove", id: string | number): void
  (e: "change", id: string | number, patch: DraftPatch): void
  (e: "reread", id: string | number): void
}>()

// How a source is added: by the address of a page, or by a card the search of a video application found.
const mode = ref<"address" | "apps">("address")
const added = computed(() => Object.fromEntries(props.items.map((item, index) => [item.url, index + 1])))
const loaded = ref<{ text: string; mark?: string }[]>([])
let loading = false
watch(mode, async (now) => {
  if (now !== "apps" || loading || !props.loadNames) return
  loading = true
  loaded.value = await props.loadNames()
})
// The names a window gives, then those the pages of the sources write, each once.
const searchNames = computed(() => {
  const seen = new Set<string>()
  return [...loaded.value, ...props.names, ...props.items.flatMap((item) => (item.names ?? []).map((text) => ({ text })))]
    .map((name) => ({ ...name, text: name.text.trim() }))
    .filter((name) => name.text && !seen.has(name.text) && seen.add(name.text))
})

async function addCard(url: string): Promise<void> {
  if (adding.value) return
  adding.value = true
  try {
    await props.adder(url)
  } finally {
    adding.value = false
  }
}

const address = ref("")
const query = ref("")
const adding = ref(false)
// Whether the core reads the typed address as a channel of VK: such a source needs a query, a channel is read whole and
// only the query keeps its videos. The core is asked on every change of the field; an answer to an older text is dropped.
const channel = ref(false)
let asked = 0

async function isChannel(typed: string): Promise<boolean> {
  const answer = await kindOf(typed)
  return answer.kind === "site" && answer.sourceType === "vk_video"
}

watch(address, async (value) => {
  const typed = value.trim()
  const turn = ++asked
  const found = typed !== "" && await isChannel(typed)
  if (turn !== asked) return
  channel.value = found
  // A pasted `channel|query` or a channel link with `?q=` gives its query to the field of the query.
  const parts = found ? splitVkAddress(typed) : null
  if (parts?.query) {
    if (!query.value.trim()) query.value = parts.query
    address.value = parts.channel
  }
})

const ready = computed(() => address.value.trim() !== "" && (!channel.value || vkQuery(query.value) !== ""))

async function add(): Promise<void> {
  const typed = address.value.trim()
  if (!typed || adding.value) return
  adding.value = true
  try {
    // The field may have been sent before the answer of the core came: the check of a channel is made here again.
    if (await isChannel(typed)) {
      channel.value = true
      if (!vkQuery(query.value)) return
      // A refused address stays in the fields for the user to correct.
      if (await props.adder(joinVkAddress(splitVkAddress(typed).channel, query.value))) clear()
    } else if (await props.adder(typed)) {
      clear()
    }
  } finally {
    adding.value = false
  }
}

function clear(): void {
  address.value = ""
  query.value = ""
  channel.value = false
}
</script>

<template>
  <div class="sources stack">
    <SourceCard
      v-for="(item, index) in items" :key="item.id" :item="item" :profiles="profiles" :busy="busy" :place="index + 1"
      @change="emit('change', item.id, $event)" @remove="editable && emit('remove', item.id)"
      @reread="emit('reread', item.id)"
    />
    <div v-if="editable" class="stack">
      <div class="st-tabs st-tabs-wrap source-mode" role="tablist" :aria-label="t('sources.mode.label')">
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
      <AppSearch v-if="mode === 'apps'" :names="searchNames" :added="added" :busy="busy || adding" @add="addCard" />
      <!-- A channel of VK stays one field: its address and its query are the two parts of it. -->
      <StGroup v-if="mode === 'address'" class="source-add">
        <StIcon icon="pi pi-link" />
        <StInput v-model="address" :label="t('sources.add.label')" @keyup.enter="add" />
        <template v-if="channel">
          <StIcon icon="pi pi-search" />
          <StInput v-model="query" class="source-query" :label="t('sources.vk.query')" @keyup.enter="add" />
        </template>
        <div class="item-button-group">
          <button type="button" class="btn-icon btn-add btn-text-icon" :disabled="busy || adding || !ready" @click="add">
            <i class="pi" :class="adding ? 'pi-spin pi-spinner' : 'pi-plus'" /><span>{{ t("sources.add.button") }}</span>
          </button>
        </div>
      </StGroup>
      <small v-if="mode === 'address' && channel" class="source-hint">{{ t("sources.vk.hint") }}</small>
    </div>
    <div v-if="error" class="source-error">{{ error }}</div>
  </div>
</template>

<style scoped>
.source-hint { color: var(--text-muted); font-size: smaller; }
.source-error { color: var(--color-red-1); overflow-wrap: anywhere; }
</style>
