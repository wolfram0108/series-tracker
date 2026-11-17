<script setup lang="ts">
import { t } from "../i18n"
import { SUBTITLE_TYPES } from "../lib/ruleVocabulary"

// The type of subtitles — full («П») or signs («Н») — as two pressed buttons: the one chosen again is taken away (no type).
// One look wherever a type is given: a folder of the torrent editor, the «ТО» of a folder of subtitles in a rule.
type SubtitleType = (typeof SUBTITLE_TYPES)[number]
const props = defineProps<{ modelValue: string; disabled?: boolean; label: string }>()
const emit = defineEmits<{ "update:modelValue": ["" | SubtitleType] }>()
</script>

<template>
  <span class="torrent-types" role="group" :aria-label="label">
    <button v-for="each in SUBTITLE_TYPES" :key="each" type="button" :class="{ active: props.modelValue === each }"
            :aria-pressed="props.modelValue === each" :title="t(`torrent.type.${each}`)" :disabled="disabled"
            @click="emit('update:modelValue', props.modelValue === each ? '' : each)">{{ t(`torrent.type.short.${each}`) }}</button>
  </span>
</template>
