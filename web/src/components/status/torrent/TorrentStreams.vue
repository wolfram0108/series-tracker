<script setup lang="ts">
import { computed, ref } from "vue"
import { t, tn, type MessageKey } from "../../../i18n"
import { containerGroups, foldersOf, trackCounts, type ContainerStream, type Editor, type Folder, type StreamFacts } from "../../../lib/torrent"
import { assign, setFolder, unassign } from "../../../composables/torrentCalls"
import type { Answer } from "../../../composables/sourceCalls"
import { useTorrentPick } from "../../../composables/useTorrentPick"
import TorrentFolder from "./TorrentFolder.vue"

// The editor «Контейнеры» (its board): the tracks inside the video files, files of one composition and one season shown
// as one container; a track of it picked is the track of every file of the container. The folders are those of
// «Озвучки» and «Субтитры»: a track let go on one is that voice-over or those subtitles in every file of the container.
const props = defineProps<{ editor: Editor }>()
const emit = defineEmits<{ failure: [string] }>()

type Shown = "all" | "unplaced" | "placed" | "video"
const FILTERS: readonly Shown[] = ["all", "unplaced", "placed", "video"]
const WORDS: Record<Shown, MessageKey> = { all: "torrent.filter.allTracks", unplaced: "torrent.filter.noFolder",
  placed: "torrent.filter.inFolder", video: "torrent.filter.video" }
const filter = ref<Shown>("all")
const entries = computed(() => props.editor.view.tabs.streams)
const byItem = computed(() => new Map(entries.value.map((entry) => [entry.item, entry] as const)))
const counts = computed(() => {
  const tracks = trackCounts(entries.value)
  const video = props.editor.view.containers.length
  return { all: entries.value.length + video, unplaced: tracks.unplaced, placed: tracks.placed, video }
})
const fits = (stream: ContainerStream): boolean => filter.value === "all" || (filter.value === "video" ? stream.facts.kind === "video"
  : stream.facts.kind !== "video" && (filter.value === "placed" ? !!stream.folder : stream.unplaced))
const groups = computed(() => containerGroups(props.editor.view.containers, entries.value)
  .map((group) => ({ ...group, shown: group.streams.filter(fits) })).filter((group) => group.shown.length))
const groupTitle = (group: (typeof groups.value)[number]): string => group.season === null
  ? t("torrent.streams.groupNoSeason", { files: tn("torrent.files", group.files) }) : t("torrent.streams.group", { season: group.season, episodes: group.episodes })

const CHANNELS: Record<number, string> = { 1: "1.0", 2: "2.0", 6: "5.1", 8: "7.1" }
function factsOf(facts: StreamFacts): string {
  const shape = facts.kind === "video" ? (facts.height ? `${facts.height}p` : "")
    : facts.channels ? CHANNELS[facts.channels] ?? String(facts.channels) : ""
  return [facts.codec, shape].filter(Boolean).join(" ")
}
const dotOf = (stream: ContainerStream): string => stream.facts.kind === "video" ? "is-notInvolved" : stream.unplaced ? "is-unplaced" : "is-placed"

const pick = useTorrentPick()
const chosenKinds = computed(() => new Set([...pick.chosen.value].map((item) => byItem.value.get(item)?.kind)))
const fitsFolder = (folder: Folder): boolean => chosenKinds.value.size === 1
  && chosenKinds.value.has(folder.kind === "voice" ? "audio" : "subtitles")

const folders = computed(() => [...foldersOf(props.editor.view.folders, entries.value, "voice"),
  ...foldersOf(props.editor.view.folders, entries.value, "subtitles")])
const languages = computed(() => [...new Set(entries.value.map((entry) => entry.language).filter(Boolean))])
// What a folder holds: its tracks of containers, and the external files of the other tab laid in it.
function holds(folder: Folder & { held: number }): string {
  const tab = folder.kind === "voice" ? "voices" : "subtitles"
  const external = props.editor.view.tabs[tab].filter((entry) => entry.folder === folder.name && !entry.excluded).length
  const tracks = t("torrent.folder.tracks", { n: folder.held })
  return external ? `${tracks} · ${t("torrent.folder.external", { n: external, tab: t(`torrent.tab.${tab}` as MessageKey) })}` : tracks
}

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
const toFolder = (name: string, items: string[]) => send(() => assign({ url: url.value, tab: "streams", items, folder: name }))
const manualChosen = computed(() => [...pick.chosen.value].filter((item) => byItem.value.get(item)?.by === "manual"))
const back = () => send(() => unassign(url.value, "streams", manualChosen.value))

const naming = ref<Folder["kind"] | null>(null)
const name = ref("")
async function create(): Promise<void> {
  const value = name.value.trim()
  if (!value || !naming.value) return
  busy.value = true
  try {
    const answer = await setFolder({ url: url.value, kind: naming.value, name: value })
    if (answer.ok) {
      naming.value = null
      name.value = ""
    } else emit("failure", answer.text)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="torrent-cols torrent-streams">
    <section class="torrent-pane">
      <div class="torrent-pane-head"><b>{{ t("torrent.streams.title") }}</b><span class="torrent-pane-hint" :title="t('torrent.streams.hint')">{{ t("torrent.streams.hint") }}</span></div>
      <div class="torrent-filters" role="group">
        <button v-for="each in FILTERS" :key="each" type="button" class="torrent-filter" :class="{ active: filter === each }"
                :aria-pressed="filter === each" @click="filter = each">
          <i v-if="each !== 'all'" class="torrent-dot" :class="each === 'video' ? 'is-notInvolved' : `is-${each}`" aria-hidden="true" />
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
               :class="{ 'is-chosen': stream.items.length && pick.allOf(stream.items), 'is-unplaced': stream.unplaced }">
            <template v-if="stream.items.length">
              <span class="torrent-grip" draggable="true" :title="t('torrent.drag')" aria-hidden="true"
                  @dragstart="pick.dragStart($event, stream.items)" @dragend="pick.dragEnd"><i class="pi pi-ellipsis-v" /></span>
              <input type="checkbox" :checked="pick.allOf(stream.items)" :aria-label="stream.label" @change="pick.toggleAll(stream.items)">
            </template>
            <i class="torrent-dot" :class="dotOf(stream)" aria-hidden="true" />
            <span class="torrent-stream-facts">{{ stream.label }}<template v-if="factsOf(stream.facts)"> · {{ factsOf(stream.facts) }}</template></span>
            <span v-if="stream.facts.kind === 'video'" class="torrent-name torrent-pane-hint">{{ t("torrent.streams.video") }}</span>
            <span v-else class="torrent-name">
              <template v-if="stream.facts.title">«{{ stream.facts.title }}» <span class="torrent-meta">{{ t("torrent.meta") }}</span></template>
              <template v-else>{{ t("torrent.streams.noTitle") }}</template>
              <template v-if="stream.facts.language && stream.facts.language !== 'und'"> · {{ stream.facts.language }} <span class="torrent-meta">{{ t("torrent.meta") }}</span></template>
              <template v-else> · <span class="torrent-tg is-problem">{{ t("torrent.track.noLanguage") }}</span></template>
            </span>
            <span v-if="stream.facts.kind !== 'video'" class="torrent-tg">{{ stream.folder ?? "—" }}</span>
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
        <b>{{ t("torrent.folders.streams") }}</b><span class="torrent-pane-hint" :title="t('torrent.folders.streamsHint')">{{ t("torrent.folders.streamsHint") }}</span>
        <div class="torrent-pane-tools">
          <button type="button" class="torrent-mini is-primary" @click="naming = 'voice'">+ {{ t("torrent.folder.addVoice") }}</button>
          <button type="button" class="torrent-mini is-primary" @click="naming = 'subtitles'">+ {{ t("torrent.folder.addSubtitles") }}</button>
        </div>
      </div>
      <form v-if="naming" class="torrent-new" @submit.prevent="create">
        <label>{{ t("torrent.folder.name") }} <input v-model="name" class="p-inputtext" required></label>
        <button type="submit" class="torrent-mini is-primary" :disabled="busy">{{ t("torrent.folder.create") }}</button>
        <button type="button" class="torrent-mini" @click="naming = null">{{ t("common.cancel") }}</button>
      </form>
      <div class="torrent-pane-body">
        <TorrentFolder v-for="folder in folders" :key="`${folder.kind}:${folder.name}`" :folder="folder" :url="url" :pick="pick"
                       :holds="holds(folder)" :languages="languages" show-kind :fits="fitsFolder(folder)"
                       @drop="toFolder(folder.name, $event)" @failure="emit('failure', $event)" />
        <p v-if="!folders.length" class="torrent-note">{{ t("torrent.folders.none") }}</p>
      </div>
    </section>
  </div>
</template>
