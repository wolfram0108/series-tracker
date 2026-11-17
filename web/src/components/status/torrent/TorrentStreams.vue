<script setup lang="ts">
import { computed, ref } from "vue"
import { t, tn, type MessageKey } from "../../../i18n"
import { containerGroups, trackCounts, type ContainerStream, type Editor, type Folder, type StreamTab } from "../../../lib/torrent"
import { assign, autoLayout, setFolder, unassign } from "../../../composables/torrentCalls"
import type { Answer } from "../../../composables/sourceCalls"
import { useTorrentPick } from "../../../composables/useTorrentPick"
import TorrentFolders from "./TorrentFolders.vue"
import TorrentLine from "./TorrentLine.vue"

// The editors «Озв. конт.» and «Суб. конт.»: the sound or the subtitles inside the video files, files of one composition and
// one season shown as one container with its video; a track of it picked is the track of every file of the container. The
// folders are those of «Озвучки» or «Субтитры»: a track let go on one is that voice-over or those subtitles in every file.
const props = defineProps<{ editor: Editor; tab: StreamTab }>()
const emit = defineEmits<{ failure: [string] }>()

type Shown = "all" | "unplaced" | "placed" | "video"
const FILTERS: readonly Shown[] = ["all", "unplaced", "placed", "video"]
const WORDS: Record<Shown, MessageKey> = { all: "torrent.filter.allTracks", unplaced: "torrent.filter.noFolder",
  placed: "torrent.filter.inFolder", video: "torrent.filter.video" }
const filter = ref<Shown>("all")
const kind = computed<Folder["kind"]>(() => props.tab === "voice_streams" ? "voice" : "subtitles")
// The stream kind of the tab, as the file names it.
const streamKind = computed(() => props.tab === "voice_streams" ? "audio" : "subtitle")
const entries = computed(() => props.editor.view.tabs[props.tab])
const byItem = computed(() => new Map(entries.value.map((entry) => [entry.item, entry] as const)))
const counts = computed(() => {
  const tracks = trackCounts(entries.value)
  const video = props.editor.view.containers.length
  return { all: entries.value.length + video, unplaced: tracks.unplaced, placed: tracks.placed, video }
})
const ofTab = (stream: ContainerStream): boolean => stream.facts.kind === "video" || stream.facts.kind === streamKind.value
const fits = (stream: ContainerStream): boolean => ofTab(stream) && (filter.value === "all" || (filter.value === "video"
  ? stream.facts.kind === "video" : stream.facts.kind !== "video" && (filter.value === "placed" ? !stream.unplaced : stream.unplaced)))
const groups = computed(() => containerGroups(props.editor.view.containers, entries.value)
  .map((group) => ({ ...group, shown: group.streams.filter(fits) })).filter((group) => group.shown.length))
const groupTitle = (group: (typeof groups.value)[number]): string => group.season === null
  ? t("torrent.streams.groupNoSeason", { files: tn("torrent.files", group.files) }) : t("torrent.streams.group", { season: group.season, episodes: group.episodes })

// A row is painted by its state: red a track in no folder (it goes no further), green placed, plain the video.
const stateOf = (stream: ContainerStream): string => stream.facts.kind === "video" ? "is-notInvolved" : stream.unplaced ? "is-unplaced" : "is-placed"

const pick = useTorrentPick()

const url = computed(() => props.editor.url)
const busy = ref(false)
async function send(call: () => Promise<Answer<unknown>>): Promise<void> {
  busy.value = true
  try {
    const answer = await call()
    if (answer.ok) pick.clear()
    else emit("failure", answer.text)
  } finally {
    busy.value = false
  }
}
const toFolder = (name: string, items: string[]) => send(() => assign({ url: url.value, tab: props.tab, items, folder: name }))
const manualChosen = computed(() => [...pick.chosen.value].filter((item) => byItem.value.get(item)?.by === "manual"))
const back = () => send(() => unassign(url.value, props.tab, manualChosen.value))
// «Авто»: the automatic layout makes again what the user took from this tab; the hand's placements stay.
const hiddenHere = computed(() => props.editor.view.hidden.some((each) => each.tab === props.tab)
  || props.editor.view.removed.some((each) => each.tab === props.tab))
const toAuto = () => send(() => autoLayout(url.value, [props.tab]))

const naming = ref(false)
const name = ref("")
async function create(): Promise<void> {
  const value = name.value.trim()
  if (!value) return
  busy.value = true
  try {
    const answer = await setFolder({ url: url.value, tab: props.tab, name: value })
    if (answer.ok) {
      naming.value = false
      name.value = ""
    } else emit("failure", answer.text)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="torrent-cols torrent-streams" :class="`torrent-${tab}`">
    <section class="torrent-pane">
      <div class="torrent-pane-head"><b>{{ t(`torrent.tab.${tab}`) }}</b><span class="torrent-pane-hint" :title="t('torrent.streams.hint')">{{ t("torrent.streams.hint") }}</span></div>
      <div class="torrent-filters" role="group">
        <button v-for="each in FILTERS" :key="each" type="button" class="torrent-filter" :class="{ active: filter === each }"
                :aria-pressed="filter === each" @click="filter = each">
          {{ t(WORDS[each]) }} <b>{{ counts[each] }}</b>
        </button>
      </div>
      <div class="torrent-pane-body">
        <template v-for="group in groups" :key="group.key">
          <div class="torrent-row is-group">
            <span class="torrent-name">{{ groupTitle(group) }}</span>
            <span class="torrent-side">{{ t("torrent.streams.files", { files: tn("torrent.files", group.files) }) }}</span>
          </div>
          <div v-for="stream in group.shown" :key="`${group.key}:${stream.label}`" class="torrent-row is-nested stream-row"
               :class="[stateOf(stream), { 'is-chosen': stream.items.length && pick.allOf(stream.items) }]">
            <template v-if="stream.items.length">
              <span class="torrent-grip" draggable="true" :title="t('torrent.drag')" aria-hidden="true"
                  @dragstart="pick.dragStart($event, stream.items)" @dragend="pick.dragEnd"><i class="pi pi-ellipsis-v" /></span>
              <input type="checkbox" :checked="pick.allOf(stream.items)" :aria-label="stream.facts.line ?? stream.label" @change="pick.toggleAll(stream.items)">
            </template>
            <TorrentLine :line="stream.facts.line ?? stream.label" />
          </div>
        </template>
        <p v-if="!groups.length" class="torrent-note">{{ t("torrent.empty") }}</p>
      </div>
      <div v-if="pick.count.value" class="torrent-pick">
        {{ t("torrent.select.count", { n: pick.count.value }) }}
        <div class="torrent-pick-tools">
          <button v-if="manualChosen.length" type="button" class="torrent-mini" :disabled="busy" @click="back">{{ t("torrent.unassign") }}</button>
          <button type="button" class="torrent-mini" @click="pick.clear">{{ t("torrent.select.clear") }}</button>
        </div>
      </div>
    </section>

    <section class="torrent-pane">
      <div class="torrent-pane-head">
        <b>{{ t(kind === "voice" ? "torrent.folders.voices" : "torrent.folders.subtitles") }}</b>
        <span class="torrent-pane-hint" :title="t(kind === 'voice' ? 'torrent.folders.voiceStreamsHint' : 'torrent.folders.subtitleStreamsHint')">
          {{ t(kind === "voice" ? "torrent.folders.voiceStreamsHint" : "torrent.folders.subtitleStreamsHint") }}</span>
        <div class="torrent-pane-tools">
          <button type="button" class="torrent-mini do-auto" :disabled="busy || !hiddenHere" :title="t('torrent.autoHint')"
                  @click="toAuto">{{ t("torrent.auto") }}</button>
          <button type="button" class="torrent-mini is-primary do-add-folder" @click="naming = true">
            + {{ t(kind === "voice" ? "torrent.folder.addVoice" : "torrent.folder.addSubtitles") }}</button>
        </div>
      </div>
      <form v-if="naming" class="torrent-new" @submit.prevent="create">
        <label>{{ t("torrent.folder.name") }} <input v-model="name" class="p-inputtext" required></label>
        <button type="submit" class="torrent-mini is-primary" :disabled="busy">{{ t("torrent.folder.create") }}</button>
        <button type="button" class="torrent-mini" @click="naming = false">{{ t("common.cancel") }}</button>
      </form>
      <div class="torrent-pane-body">
        <TorrentFolders :editor="editor" :tab="tab" :pick="pick" :fits="() => true"
                        @drop="(_, name, items) => toFolder(name, items)" @failure="emit('failure', $event)" />
        <p v-if="!editor.view.folders.some((each) => each.tab === tab)" class="torrent-note">{{ t("torrent.folders.none") }}</p>
      </div>
    </section>
  </div>
</template>
