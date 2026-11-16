<script setup lang="ts">
import { computed, ref, watch } from "vue"
import { useToast } from "primevue/usetoast"
import Skeleton from "primevue/skeleton"
import FoldButton from "./FoldButton.vue"
import SheetField from "./SheetField.vue"
import StInput from "./StInput.vue"
import StSelect from "./StSelect.vue"
import StValue from "./StValue.vue"
import PowerSwitch from "./status/output/PowerSwitch.vue"
import SourceMark from "./status/output/SourceMark.vue"
import { t } from "../i18n"
import { reasonText } from "../lib/reason"
import {
  joinVkAddress, vkQuery, placeOf, profileOptions, progressText, seasonPositions, sourceCaption, spanText, splitVkAddress,
  stepsLines, togglePlayer, type SourceItem,
} from "../lib/sources"
import type { DraftPatch } from "../composables/useSourceDrafts"
import { SOURCE_STATES, stateLabel } from "../lib/states"

// One source of a series: a head with its mark, its place, its state and its actions; its address and its constructor
// profile; and its players as tabs, each with the table of the voice-overs the rules of the profile made of its rows.
// The numbers of episodes exist only as the constructor's answer; a group is made in the settings, nowhere else.
// `place` is the place of the source in the list of its series: with its colour, the mark the output tab shows it by.
// `waiting`: the tables of the sources are being read; the card shows that in place of the state and the table it has not got yet.
const props = defineProps<{ item: SourceItem; profiles: { id: number; name: string }[]; busy?: boolean; waiting?: boolean; place?: number }>()
const emit = defineEmits<{
  (e: "change", patch: DraftPatch): void; (e: "remove"): void; (e: "reread"): void; (e: "stop"): void
}>()
const toast = useToast()

const isTorrent = computed(() => props.item.sourceType === "torrent")
const isChannel = computed(() => props.item.sourceType === "vk_video")
// A card of a video application has no page a browser opens: its address is shown and copied, with no link to open.
const appCard = computed(() => props.item.url.startsWith("app://"))
const view = computed(() => props.item.view)
const options = computed(() => profileOptions(props.profiles))
const seasonOptions = (groups: number) => [
  { label: t("sources.vibix.none"), value: null as number | null },
  ...seasonPositions(groups).map((position) => ({ label: t("sources.vibix.option", { position }), value: position as number | null })),
]

const stateText = computed(() => {
  const state = view.value.state
  if (state === "reading" && view.value.progress) return progressText(view.value.progress)
  return stateLabel(SOURCE_STATES[state])
})

// The player whose tab is open; the first one until the user picks another, and again when the picked one is gone.
const picked = ref<string | null>(null)
const player = computed(() => view.value.players.find((each) => each.player === picked.value) ?? view.value.players[0] ?? null)

// The channel and its query are edited as text and sent when the field is left, so that typing starts no read.
const channel = ref("")
const query = ref("")
watch(() => props.item.url, (url) => {
  if (!isChannel.value) return
  const parts = splitVkAddress(url)
  channel.value = parts.channel
  query.value = parts.query
}, { immediate: true })

// The rows of each line of an application card a read takes: `all`, `10` (the first ten), `-10` (the last ten), `50-60`
// (from the 50th to the 60th). It is edited as text and sent when the field is left, and a value that is none of them is
// not sent.
const rows = ref("")
watch(() => props.item.rowsPerLine, (value) => { rows.value = value }, { immediate: true })
const rowsText = computed(() => rows.value.replace(/\s+/g, "").replace(/[–—]/g, "-").toLowerCase())
const rowsValid = computed(() => {
  const span = /^([1-9][0-9]*)-([1-9][0-9]*)$/.exec(rowsText.value)
  return span ? Number(span[1]) <= Number(span[2]) : /^(all|-?[1-9][0-9]*)?$/.test(rowsText.value)
})

function commitRows(): void {
  const value = rowsText.value || "all"
  if (rowsValid.value && value !== props.item.rowsPerLine) emit("change", { rowsPerLine: value })
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
  <article class="source-card fold-room">
    <header class="source-head">
      <span class="source-title">
        <SourceMark v-if="place !== undefined && item.color !== null" :mark="{ n: place, color: item.color, place: '' }" />
        <span class="source-place">{{ view.title || placeOf(item.url) }}</span>
      </span>
      <template v-if="!isTorrent">
        <span v-if="waiting" class="source-state" data-state="read"><i class="pi pi-spin pi-spinner" />{{ t("sources.tableLoading") }}</span>
        <span v-else class="source-state" :data-state="SOURCE_STATES[view.state]">
          <i v-if="view.state === 'reading'" class="pi pi-spin pi-spinner" />{{ stateText }}
        </span>
        <span v-if="view.players.length && !waiting" class="muted">{{ sourceCaption(view.players) }}</span>
        <span class="source-actions">
          <!-- One place for both: while the page is read the button that reads it again is the one that stops the read. -->
          <FoldButton v-if="view.state === 'reading'" :label="t('sources.stopRead')" icon="pi pi-stop-circle" severity="danger"
                      class="source-stop" @click="emit('stop')" />
          <FoldButton v-else :label="t('sources.reread')" icon="pi pi-refresh" severity="warn" class="source-reread" :disabled="busy || waiting"
                      @click="emit('reread')" />
          <FoldButton :label="t('sources.remove')" icon="pi pi-trash" severity="danger" class="source-remove" :disabled="busy || waiting"
                      @click="emit('remove')" />
        </span>
      </template>
    </header>

    <div class="source-body stack">
      <div v-if="isChannel" class="stack">
        <small class="muted">{{ t("sources.vk.hint") }}</small>
        <div class="grid-fit" @focusout="commitChannel" @keyup.enter="commitChannel">
          <SheetField :label="t('sources.vk.channel')" icon="pi pi-youtube">
            <StInput v-model="channel" />
          </SheetField>
          <SheetField :label="t('sources.vk.query')" icon="pi pi-search">
            <StInput v-model="query" :placeholder="t('sources.vk.queryHint')" />
          </SheetField>
        </div>
        <small v-if="!vkQuery(query)" class="source-reason">{{ t("sources.vk.queryMissing") }}</small>
      </div>
      <SheetField v-else-if="appCard" class="source-app-card is-readonly" :label="t('sources.apps.card')" icon="pi pi-video">
        <StValue :value="item.url" mono />
        <div class="constructor-item url-actions">
          <button type="button" class="url-action-btn" :title="t('sources.address.copy')" @click="copyAddress">
            <i class="pi pi-clipboard" />
          </button>
        </div>
      </SheetField>
      <div v-if="appCard" class="stack source-rows" @focusout="commitRows" @keyup.enter="commitRows">
        <SheetField :label="t('sources.rows.label')" icon="pi pi-list">
          <StInput v-model="rows" />
        </SheetField>
        <small v-if="rowsValid" class="muted">{{ t("sources.rows.hint") }}</small>
        <small v-else class="source-reason">{{ t("sources.rows.invalid") }}</small>
      </div>
      <SheetField v-if="!isChannel && !appCard" class="is-readonly" :label="t('sources.address.label')" icon="pi pi-link">
        <StValue :value="item.url" mono />
        <div class="constructor-item url-actions">
          <button type="button" class="url-action-btn" :title="t('sources.address.copy')" @click="copyAddress">
            <i class="pi pi-clipboard" />
          </button>
          <a class="url-action-btn" :href="item.url" target="_blank" rel="noopener" :title="t('sources.address.open')">
            <i class="pi pi-external-link" />
          </a>
        </div>
      </SheetField>

      <SheetField :label="t('sources.profile.label')" icon="pi pi-filter">
        <StSelect
          :model-value="item.profileId" :options="options" :placeholder="t('sources.profile.none')"
          @update:model-value="emit('change', { profileId: $event as number | null })"
        />
      </SheetField>
      <small v-if="item.profileId === null && !isTorrent" class="muted">{{ t("sources.profile.hint") }}</small>
      <div v-if="view.reason && !waiting" class="source-reason">{{ reasonText(view.reason) }}</div>
      <div v-if="waiting && !isTorrent" class="source-table-wait stack" role="status" aria-busy="true" :aria-label="t('sources.tableLoading')">
        <Skeleton width="14rem" height="2rem" border-radius="var(--border-radius)" />
        <Skeleton v-for="line in 4" :key="line" height="1.6rem" border-radius="0.4rem" />
      </div>

      <div v-if="!isTorrent && player" class="players stack">
        <div class="st-tabs st-tabs-wrap" role="tablist" :aria-label="t('sources.player.tabs')">
          <button
            v-for="each in view.players" :key="each.player" type="button" class="st-tab player-tab" role="tab"
            :class="{ active: each.player === player.player, 'is-off': each.off, 'is-stale': each.allStale }" :aria-selected="each.player === player.player"
            @click="picked = each.player"
          >
            <span class="player-name">{{ each.title }}</span><small class="player-rows">{{ each.rows }}</small>
          </button>
        </div>
        <div class="player stack" :class="{ 'is-off': player.off }" role="tabpanel" :data-player="player.player">
          <div class="player-top">
            <span v-if="player.off" class="muted">{{ t("sources.player.off") }}</span>
            <span v-for="line in player.coverage" :key="spanText(line)" class="player-fact">
              {{ t("sources.table.episodes") }} <b>{{ spanText(line) }}</b>
              <span v-if="line.gaps" class="player-gaps">{{ t("sources.range.gaps", { gaps: line.gaps }) }}</span>
            </span>
            <span v-if="player.steps" class="player-fact">{{ t("sources.table.resolution") }} <b>{{ player.steps }}</b></span>
            <span class="player-fact">{{ t("sources.table.rows") }} <b>{{ player.rows }}</b></span>
            <PowerSwitch
              class="player-switch" :on="!player.off" :busy="busy" :label="t('sources.player.use', { player: player.title })"
              @toggle="emit('change', { playersOff: togglePlayer(item.playersOff, player.player) })"
            />
          </div>
          <SheetField v-if="player.chooseSeason" class="player-season" :label="t('sources.vibix.season')" icon="pi pi-th-large">
            <StSelect
              :model-value="item.vibixSeason" :options="seasonOptions(player.groups)" :placeholder="t('sources.vibix.none')"
              @update:model-value="emit('change', { vibixSeason: $event as number | null })"
            />
          </SheetField>
          <small v-if="player.chooseSeason && item.vibixSeason === null" class="muted">{{ t("sources.vibix.hint") }}</small>
          <div v-if="player.voices.length" class="voice-scroll">
            <table class="voice-table">
              <thead>
                <tr>
                  <th>{{ t("sources.table.group") }}</th>
                  <th>{{ t("sources.table.voice") }}</th>
                  <th>{{ t("sources.table.episodes") }}</th>
                  <th>{{ t("sources.table.resolution") }}</th>
                  <th class="voice-rows">{{ t("sources.table.rows") }}</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="voice in player.voices" :key="`${voice.name}\u0000${voice.group ?? ''}`"
                    :class="{ 'is-stale': voice.allStale }"
                    :title="voice.stale.length ? t('sources.stale', { episodes: voice.stale.join(', ') }) : undefined">
                  <td>{{ voice.group ?? t("sources.voice.noGroup") }}</td>
                  <td>{{ voice.name }}</td>
                  <td>
                    <div v-for="line in voice.coverage" :key="spanText(line)">
                      {{ spanText(line) }}
                      <span v-if="line.gaps" class="player-gaps voice-gaps">{{ t("sources.range.gaps", { gaps: line.gaps }) }}</span>
                    </div>
                    <div v-if="voice.stale.length && !voice.allStale"><span class="stale-episodes">{{ voice.stale.join(", ") }}</span></div>
                  </td>
                  <td><div v-for="line in stepsLines(voice.coverage)" :key="line" class="voice-steps">{{ line }}</div></td>
                  <td class="voice-rows">{{ voice.rows }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <small v-else-if="item.profileId !== null && view.state === 'ready' && !player.off" class="muted">
            {{ t("sources.range.none") }}
          </small>
        </div>
      </div>
      <div v-else-if="!isTorrent && view.state === 'ready'" class="muted">{{ t("sources.player.none") }}</div>
    </div>
  </article>
</template>

<style scoped>
.source-card {
  border: var(--border-width) solid var(--border-color); border-radius: var(--card-border-radius); background: var(--bg-white);
  box-shadow: var(--card-shadow);
}
/* The head of a source is the head of a season of the output tab: a band above the fields. */
.source-head {
  display: flex; flex-wrap: wrap; align-items: center; gap: 0.5rem 0.75rem; padding: 0.6rem 0.75rem;
  background: linear-gradient(135deg, var(--bg-light) 0%, var(--border-color) 100%); border-bottom: var(--border-width) solid var(--border-color);
  border-radius: var(--card-border-radius) var(--card-border-radius) 0 0;
}
.source-title { display: inline-flex; align-items: center; gap: 0.5rem; min-inline-size: 0; font-weight: 600; color: #2c3e50; }
.source-place { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.source-state { display: inline-flex; align-items: center; gap: var(--space); color: color-mix(in srgb, var(--tone-end) 60%, black); }
.source-actions { display: flex; flex-wrap: wrap; gap: 0.5rem; margin-inline-start: auto; }
/* The buttons of the head are those of the main screen at the height of the head of a season. */
.source-body { padding: var(--space); }
.source-reason { color: var(--color-red-1); overflow-wrap: anywhere; }
.muted { color: var(--text-muted); font-size: smaller; }
.player-tab { padding-block: 0.3rem; font-size: 0.85rem; }
.player-rows { color: var(--text-muted); font-weight: 500; font-variant-numeric: tabular-nums; }
.player-tab.is-off .player-name { text-decoration: line-through; }
.player-tab.is-stale { background: var(--stale-ground); border-bottom: 0.125rem solid var(--stale-mark); }
.player-top { display: flex; flex-wrap: wrap; align-items: center; gap: 0.375rem 0.9rem; font-size: 0.875rem; color: var(--color-gray-600); }
.player-top b { color: #2c3e50; }
/* The switch of a player stands in the corner, as every switch of the window does. */
.player-switch { margin-inline-start: auto; }
.player-gaps { color: var(--text-muted); font-size: 0.8rem; }
.player-fact .player-gaps { margin-inline-start: 0.375rem; }
.voice-gaps { display: block; }
.player-season { max-inline-size: 36rem; }
.voice-scroll { overflow-x: auto; }
.voice-table { inline-size: 100%; border-collapse: collapse; border: var(--border-width) solid var(--border-color); font-size: 0.875rem; }
.voice-table th {
  background: var(--bg-light); font-weight: 700; text-align: start; padding: 0.45rem 0.6rem; color: #2c3e50;
  border-bottom: var(--border-width) solid var(--border-color); white-space: nowrap;
}
.voice-table td { padding: 0.4rem 0.6rem; border-bottom: var(--border-width) solid var(--border-color); vertical-align: top; }
.voice-table tbody tr:nth-child(odd) { background: rgba(0, 0, 0, 0.02); }
.voice-steps { white-space: nowrap; }
.voice-rows { inline-size: 1%; text-align: end; font-variant-numeric: tabular-nums; white-space: nowrap; }
.player.is-off .voice-table, .player.is-off .player-fact { opacity: 0.5; }
</style>
