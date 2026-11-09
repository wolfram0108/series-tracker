<script setup lang="ts">
import { computed } from "vue"
import { t } from "../../../i18n"

// The place of a voice-over among the tracks, with the corner of its presence (styles/pills.css .presence): online while a
// source gives it, offline when only the files hold it; none when presence is not known there.
const props = withDefaults(defineProps<{ n: number; online?: boolean | null }>(), { online: null })
const presence = computed(() => props.online == null ? null : props.online ? "is-online" : "is-offline")
const title = computed(() => props.online == null ? undefined : t(props.online ? "output.presence.online" : "output.presence.offline"))
</script>

<template>
  <span class="figure-mark" :class="presence && ['presence', presence]" :title="title">{{ n }}</span>
</template>
