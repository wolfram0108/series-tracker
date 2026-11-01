<script setup lang="ts">
import { ref } from "vue"
import { useVoiceGroups } from "../composables/useVoiceGroups"
import { t } from "../i18n"

// A voice-over that is in no group can be given one where it is shown: the name typed here is the name of the group,
// and the names of the voice-over (`names`) are what puts a voice-over into it. A voice-over with no name of its own
// cannot be put into a group by name; it is named through its source.
const props = defineProps<{ names: string[]; disabled?: boolean }>()
const { busy, makeGroup } = useVoiceGroups()
const name = ref("")
const failure = ref("")

async function make(): Promise<void> {
  failure.value = ""
  const refused = await makeGroup(name.value, props.names)
  if (refused === null) name.value = ""
  else failure.value = refused
}
</script>

<template>
  <div v-if="names.length > 0" class="group-maker stack">
    <div class="cluster" @keyup.enter="make">
      <input
        v-model="name" class="group-maker-name" type="text" :placeholder="t('voices.group.name')"
        :aria-label="t('voices.group.name')" :disabled="busy || disabled"
      />
      <button type="button" class="group-maker-make" :disabled="busy || disabled || !name.trim()" @click="make">
        {{ t("voices.group.make") }}
      </button>
    </div>
    <small v-if="failure" class="group-maker-failure" role="alert">{{ failure }}</small>
  </div>
  <small v-else class="group-maker-nameless">{{ t("voices.group.nameless") }}</small>
</template>

<style scoped>
.group-maker-name { flex: 1 1 var(--measure); min-inline-size: 0; max-inline-size: 100%; }
.group-maker-failure { color: var(--color-red-1); overflow-wrap: anywhere; }
.group-maker-nameless { color: var(--text-muted); }
.group-maker-make { cursor: pointer; }
.group-maker-make:disabled { cursor: default; opacity: 0.5; }
</style>
