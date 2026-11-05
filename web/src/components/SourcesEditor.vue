<script setup lang="ts">
import { computed, ref, watch } from "vue"
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
}>(), { error: "", busy: false, editable: true })

const emit = defineEmits<{
  (e: "remove", id: string | number): void
  (e: "change", id: string | number, patch: DraftPatch): void
  (e: "reread", id: string | number): void
}>()

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
      v-for="item in items" :key="item.id" :item="item" :profiles="profiles" :busy="busy"
      @change="emit('change', item.id, $event)" @remove="editable && emit('remove', item.id)"
      @reread="emit('reread', item.id)"
    />
    <div v-if="editable" class="stack">
      <div class="source-add cluster">
        <!-- A channel of VK stays one field: its address and its query are the two parts of it. -->
        <StGroup>
          <StIcon icon="pi pi-link" />
          <StInput v-model="address" :label="t('sources.add.label')" @keyup.enter="add" />
          <template v-if="channel">
            <StIcon icon="pi pi-search" />
            <StInput v-model="query" class="source-query" :label="t('sources.vk.query')" @keyup.enter="add" />
          </template>
        </StGroup>
        <button type="button" class="source-action" :disabled="busy || adding || !ready" @click="add">
          <i class="pi" :class="adding ? 'pi-spin pi-spinner' : 'pi-plus'" />{{ t("sources.add.button") }}
        </button>
      </div>
      <small v-if="channel" class="source-hint">{{ t("sources.vk.hint") }}</small>
    </div>
    <div v-if="error" class="source-error">{{ error }}</div>
  </div>
</template>

<style scoped>
.source-add > :first-child { flex: 1 1 var(--measure); }
.source-hint { color: var(--text-muted); font-size: smaller; }
.source-error { color: var(--color-red-1); overflow-wrap: anywhere; }
</style>
