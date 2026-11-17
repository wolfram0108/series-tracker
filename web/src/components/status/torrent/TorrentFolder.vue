<script setup lang="ts">
import { computed, ref } from "vue"
import Select from "primevue/select"
import { t } from "../../../i18n"
import type { FolderView } from "../../../lib/torrent"
import { nameOf } from "../../../lib/torrent"
import { deleteFolder, setFolder } from "../../../composables/torrentCalls"
import type { TorrentPick } from "../../../composables/useTorrentPick"

// A folder of tracks (the boards «Озвучки», «Субтитры», «Контейнеры»): its name is the name of the voice-over or of the
// subtitles in the rows of the series; its language and type of subtitles are set here, the rule that names it is shown,
// a folder made by hand is deleted here. The pick let go on it is placed in it by hand.
const props = defineProps<{
  folder: FolderView
  url: string
  pick: TorrentPick
  /** What it holds, as the tab counts it. */
  holds: string
  languages: string[]
  /** The kind is shown where folders of both kinds stand together. */
  showKind?: boolean
  /** The pick fits the folder (a sound in a folder of a voice-over, subtitles in one of subtitles). */
  fits: boolean
}>()
const emit = defineEmits<{ drop: [string[]]; failure: [string] }>()

const TYPES = ["full", "signs", "forced"] as const
const FONTS_SHOWN = 3
const allFonts = ref(false)
const fonts = computed(() => allFonts.value ? props.folder.fonts : props.folder.fonts.slice(0, FONTS_SHOWN))
const options = computed(() => [...new Set([props.folder.language, ...props.languages].filter(Boolean))].sort())

const busy = ref(false)
async function save(change: { language?: string; subtitle_type?: "" | (typeof TYPES)[number] }): Promise<void> {
  busy.value = true
  try {
    const answer = await setFolder({ url: props.url, kind: props.folder.kind, name: props.folder.name,
      language: change.language ?? props.folder.language,
      subtitle_type: (change.subtitle_type ?? props.folder.subtitle_type) as "" | (typeof TYPES)[number] })
    if (!answer.ok) emit("failure", answer.text)
  } finally {
    busy.value = false
  }
}
async function remove(): Promise<void> {
  busy.value = true
  try {
    const answer = await deleteFolder(props.url, props.folder.kind, props.folder.name)
    if (!answer.ok) emit("failure", answer.text)
  } finally {
    busy.value = false
  }
}
const ruleText = computed(() => {
  const rule = props.folder.rule
  if (!rule) return ""
  return rule.row === null ? t("torrent.by.rule", { rule: rule.rule }) : t("torrent.by.row", { rule: rule.rule, row: rule.row })
})
const target = computed(() => props.pick.target((items) => emit("drop", items)))
const live = computed(() => props.pick.count.value > 0 && props.fits)
</script>

<template>
  <div class="torrent-folder" :data-folder="folder.name" v-bind="live ? target : {}">
    <div class="torrent-folder-head">
      <span class="torrent-folder-name">{{ folder.name }}</span>
      <span v-if="showKind" class="torrent-tag">{{ t(folder.kind === "voice" ? "torrent.folder.kind.voice" : "torrent.folder.kind.subtitles") }}</span>
      <Select :model-value="folder.language || null" :options="options" editable :disabled="busy" class="torrent-language"
              :aria-label="t('torrent.folder.language')" :placeholder="t('torrent.folder.language')"
              @update:model-value="(value: string | null) => save({ language: value ?? '' })" />
      <div v-if="folder.kind === 'subtitles'" class="torrent-types" role="group">
        <button v-for="each in TYPES" :key="each" type="button" :class="{ active: folder.subtitle_type === each }"
                :aria-pressed="folder.subtitle_type === each" :disabled="busy"
                @click="save({ subtitle_type: folder.subtitle_type === each ? '' : each })">{{ t(`torrent.type.${each}`) }}</button>
      </div>
      <span v-if="ruleText" class="pill pill-primary">{{ ruleText }}</span>
      <span v-else-if="folder.manual" class="pill pill-secondary">
        {{ t(folder.items ? "torrent.folder.manual" : "torrent.folder.manualEmpty") }}</span>
      <div class="torrent-folder-tools">
        <button v-if="folder.manual && !folder.rule" type="button" class="torrent-mini do-delete-folder" :disabled="busy" @click="remove">
          {{ t("torrent.folder.delete") }}</button>
      </div>
    </div>
    <div class="torrent-folder-meta">
      <span>{{ holds }}<template v-if="folder.episodes"> · {{ folder.episodes }}</template>
        <template v-if="folder.kind === 'subtitles' && folder.fonts.length"> · {{ t("torrent.folder.fonts", { n: folder.fonts.length }) }}</template></span>
    </div>
    <div v-if="folder.fonts.length" class="torrent-fonts">
      <span v-for="font in fonts" :key="font" :title="font">{{ nameOf(font) }}</span>
      <button v-if="folder.fonts.length > FONTS_SHOWN" type="button" class="torrent-mini" @click="allFonts = !allFonts">
        {{ allFonts ? t("common.hide") : t("torrent.folder.moreFonts", { n: folder.fonts.length - FONTS_SHOWN }) }}</button>
    </div>
    <button v-if="live || (folder.manual && !folder.items)" type="button" class="torrent-drop drop-folder" :class="{ 'is-live': live }"
            :disabled="!live" @click="emit('drop', [...pick.chosen.value])">
      <i class="pi pi-arrow-down" aria-hidden="true" />
      <span>{{ live ? t("torrent.folder.drop", { name: folder.name, n: pick.count.value }) : t("torrent.folder.dropEmpty", { name: folder.name }) }}</span>
    </button>
  </div>
</template>
