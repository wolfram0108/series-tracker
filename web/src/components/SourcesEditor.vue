<script setup lang="ts">
import { ref } from "vue"
import SourceCard from "./SourceCard.vue"
import StGroup from "./StGroup.vue"
import StIcon from "./StIcon.vue"
import StInput from "./StInput.vue"
import { t } from "../i18n"
import type { SourceItem } from "../lib/sources"
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
}>()

const address = ref("")
const adding = ref(false)

async function add(): Promise<void> {
  const typed = address.value.trim()
  if (!typed || adding.value) return
  adding.value = true
  try {
    // A refused address stays in the field for the user to correct.
    if (await props.adder(typed)) address.value = ""
  } finally {
    adding.value = false
  }
}
</script>

<template>
  <div class="sources stack">
    <SourceCard
      v-for="item in items" :key="item.id" :item="item" :profiles="profiles" :busy="busy"
      @change="emit('change', item.id, $event)" @remove="editable && emit('remove', item.id)"
    />
    <div v-if="editable" class="source-add cluster">
      <StGroup>
        <StIcon icon="pi pi-link" />
        <StInput v-model="address" :label="t('sources.add.label')" @keyup.enter="add" />
      </StGroup>
      <button type="button" class="source-action" :disabled="busy || adding || !address.trim()" @click="add">
        <i class="pi" :class="adding ? 'pi-spin pi-spinner' : 'pi-plus'" />{{ t("sources.add.button") }}
      </button>
    </div>
    <div v-if="error" class="source-error">{{ error }}</div>
  </div>
</template>

<style scoped>
.source-add > :first-child { flex: 1 1 var(--measure); }
.source-error { color: var(--color-red-1); overflow-wrap: anywhere; }
</style>
