<script setup lang="ts">
import { computed, ref, watch } from "vue"
import { useToast } from "primevue/usetoast"
import StGroup from "./StGroup.vue"
import StIcon from "./StIcon.vue"
import StInput from "./StInput.vue"
import StSelect from "./StSelect.vue"
import { t, type MessageKey } from "../i18n"
import { reasonText } from "../lib/reason"
import {
  joinVkAddress, vkQuery, playerCaption, profileOptions, progressText, seasonPositions, sourceCaption, splitVkAddress,
  togglePlayer, type SourceItem,
} from "../lib/sources"
import type { DraftPatch } from "../composables/useSourceDrafts"

// One source of a series: its address, its constructor profile, and what its table holds — players with the episodes
// and the voice-overs that the rules of the profile made of its rows, and the group of each voice-over. The numbers of
// episodes exist only as the constructor's answer; a group is made in the settings, nowhere else.
const props = defineProps<{ item: SourceItem; profiles: { id: number; name: string }[]; busy?: boolean }>()
const emit = defineEmits<{ (e: "change", patch: DraftPatch): void; (e: "remove"): void; (e: "reread"): void }>()
const toast = useToast()

const isTorrent = computed(() => props.item.sourceType === "torrent")
const isChannel = computed(() => props.item.sourceType === "vk_video")
const view = computed(() => props.item.view)
const options = computed(() => profileOptions(props.profiles))
const seasonOptions = (groups: number) => [
  { label: t("sources.vibix.none"), value: null as number | null },
  ...seasonPositions(groups).map((position) => ({ label: t("sources.vibix.option", { position }), value: position as number | null })),
]

const stateText = computed(() => {
  const state = view.value.state
  if (state === "reading" && view.value.progress) return progressText(view.value.progress)
  return t(`sources.state.${state}` as MessageKey)
})

// The channel and its query are edited as text and sent when the field is left, so that typing starts no read.
const channel = ref("")
const query = ref("")
watch(() => props.item.url, (url) => {
  if (!isChannel.value) return
  const parts = splitVkAddress(url)
  channel.value = parts.channel
  query.value = parts.query
}, { immediate: true })

// The name of the voice-over of the source is typed and sent when the field is left, like the channel.
const voiceName = ref("")
watch(() => props.item.voiceName, (name) => { voiceName.value = name }, { immediate: true })
function commitVoiceName(): void {
  const name = voiceName.value.trim()
  if (name !== props.item.voiceName) emit("change", { voiceName: name })
}

// A channel is read whole and only the query keeps its videos: an address without one is not sent.
function commitChannel(): void {
  if (!vkQuery(query.value)) return
  const url = joinVkAddress(channel.value, query.value)
  if (url !== props.item.url) emit("change", { url })
}

async function copyAddress(): Promise<void> {
  try {
    await navigator.clipboard.writeText(props.item.url)
    toast.add({ severity: "success", summary: t("sources.address.copied"), life: 2000 })
  } catch {
    /* the clipboard is not allowed: nothing to tell */
  }
}
</script>

<template>
  <article class="source-card stack">
    <header class="cluster source-head">
      <b>{{ t(`sources.type.${item.sourceType}` as MessageKey) }}</b>
      <template v-if="!isTorrent">
        <span class="source-state" :class="`is-${view.state}`">
          <i v-if="view.state === 'reading'" class="pi pi-spin pi-spinner" />{{ stateText }}
        </span>
        <span v-if="view.players.length" class="muted">{{ sourceCaption(view.players) }}</span>
        <button type="button" class="source-reread" :disabled="busy || view.state === 'reading'" @click="emit('reread')">
          <i class="pi pi-refresh" />{{ t("sources.reread") }}
        </button>
        <button type="button" class="source-remove" :disabled="busy" @click="emit('remove')">
          <i class="pi pi-trash" />{{ t("sources.remove") }}
        </button>
      </template>
    </header>

    <div v-if="isChannel" class="stack">
      <small class="muted">{{ t("sources.vk.hint") }}</small>
      <div class="grid-fit" @focusout="commitChannel" @keyup.enter="commitChannel">
        <StGroup>
          <StIcon icon="pi pi-youtube" />
          <StInput v-model="channel" :label="t('sources.vk.channel')" />
        </StGroup>
        <StGroup>
          <StIcon icon="pi pi-search" />
          <StInput v-model="query" :label="t('sources.vk.query')" />
        </StGroup>
      </div>
      <small v-if="!vkQuery(query)" class="source-reason">{{ t("sources.vk.queryMissing") }}</small>
    </div>
    <StGroup v-else>
      <StIcon icon="pi pi-link" />
      <div class="constructor-item item-floating-label"><input class="item-input" :value="item.url" readonly /></div>
      <div class="constructor-item url-actions">
        <button type="button" class="url-action-btn" :title="t('sources.address.copy')" @click="copyAddress">
          <i class="pi pi-clipboard" />
        </button>
        <a class="url-action-btn" :href="item.url" target="_blank" rel="noopener" :title="t('sources.address.open')">
          <i class="pi pi-external-link" />
        </a>
      </div>
    </StGroup>

    <StGroup>
      <div class="constructor-item item-label-icon item-label-text-icon" :title="t('sources.profile.label')">
        <i class="pi pi-filter" /><span>{{ t("sources.profile.label") }}</span>
      </div>
      <StSelect
        :model-value="item.profileId" :options="options" :placeholder="t('sources.profile.none')"
        @update:model-value="emit('change', { profileId: $event as number | null })"
      />
    </StGroup>
    <small v-if="item.profileId === null && !isTorrent" class="muted">{{ t("sources.profile.hint") }}</small>
    <div v-if="!isTorrent" class="stack source-voice-name" @focusout="commitVoiceName" @keyup.enter="commitVoiceName">
      <StGroup>
        <StIcon icon="pi pi-microphone" />
        <StInput v-model="voiceName" :label="t('sources.voiceName.label')" />
      </StGroup>
      <small class="muted">{{ t("sources.voiceName.hint") }}</small>
    </div>
    <div v-if="view.reason" class="source-reason">{{ reasonText(view.reason) }}</div>

    <ul v-if="!isTorrent && view.players.length" class="players grid-fit">
      <li v-for="player in view.players" :key="player.player" class="player stack" :class="{ 'is-off': player.off }">
        <div class="cluster">
          <label class="cluster player-switch">
            <input
              type="checkbox" class="form-switch-input" :checked="!player.off" :disabled="busy"
              :aria-label="t('sources.player.use', { player: player.title })"
              @change="emit('change', { playersOff: togglePlayer(item.playersOff, player.player) })"
            />
            <b>{{ player.title }}</b>
          </label>
          <span v-if="player.off" class="muted">{{ t("sources.player.off") }}</span>
        </div>
        <div class="cluster muted">
          <span>{{ playerCaption(player) }}</span>
        </div>
        <div v-if="player.chooseSeason" class="stack">
          <StGroup>
            <div class="constructor-item item-label-icon item-label-text-icon" :title="t('sources.vibix.season')">
              <i class="pi pi-th-large" /><span>{{ t("sources.vibix.season") }}</span>
            </div>
            <StSelect
              :model-value="item.vibixSeason" :options="seasonOptions(player.groups)" :placeholder="t('sources.vibix.none')"
              @update:model-value="emit('change', { vibixSeason: $event as number | null })"
            />
          </StGroup>
          <small v-if="item.vibixSeason === null" class="muted">{{ t("sources.vibix.hint") }}</small>
        </div>
        <ul v-if="player.coverage.length" class="lines coverage">
          <li v-for="line in player.coverage" :key="line">{{ line }}</li>
        </ul>
        <ul v-if="player.voiceLines.length" class="lines voices">
          <li v-for="line in player.voiceLines" :key="line">{{ line }}</li>
        </ul>
        <small v-if="item.profileId !== null && view.state === 'ready' && !player.coverage.length && !player.off" class="muted">
          {{ t("sources.range.none") }}
        </small>
      </li>
    </ul>
    <div v-else-if="!isTorrent && view.state === 'ready'" class="muted">{{ t("sources.player.none") }}</div>
  </article>
</template>

<style scoped>
.source-card {
  padding: var(--space); border: var(--border-width) solid var(--border-color);
  border-radius: var(--card-border-radius); background: var(--bg-white);
}
.source-head { justify-content: space-between; }
.source-head > b { margin-inline-end: auto; }
.source-state { display: inline-flex; align-items: center; gap: var(--space); color: var(--text-muted); }
.source-state.is-ready { color: var(--color-green-1); }
.source-state.is-failed { color: var(--color-red-1); }
.source-state.is-reading { color: var(--color-blue); }
.source-reason { color: var(--color-red-1); overflow-wrap: anywhere; }
.muted { color: var(--text-muted); font-size: smaller; }
.source-reread, .source-remove {
  display: inline-flex; align-items: center; gap: var(--space); cursor: pointer;
  background: none; border: none; color: var(--color-red-1);
}
.source-reread:disabled, .source-remove:disabled { cursor: default; opacity: 0.5; }
.source-reread { color: var(--color-blue); }
.players { list-style: none; margin: 0; padding: 0; }
.player { padding: var(--space); border: var(--border-width) solid var(--border-color); border-radius: var(--border-radius); }
.player.is-off .lines, .player.is-off .player-switch b { opacity: 0.5; }
.player-switch { cursor: pointer; }
.lines { list-style: none; margin: 0; padding: 0; display: grid; gap: calc(var(--space) / 2); overflow-wrap: anywhere; }
</style>
