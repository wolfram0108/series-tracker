<script setup lang="ts">
import { computed, ref } from "vue"
import { t } from "../../../i18n"
import { byEpisode, folderOf, trackCounts, trackFits, type Editor, type TrackEntry, type TrackFilter } from "../../../lib/torrent"
import { assign, autoLayout, setFolder, unassign } from "../../../composables/torrentCalls"
import type { Answer } from "../../../composables/sourceCalls"
import { useTorrentPick } from "../../../composables/useTorrentPick"
import TorrentFolders from "./TorrentFolders.vue"
import TorrentLine from "./TorrentLine.vue"

// The editors «Озвучки» and «Субтитры» (their boards): the external sound or subtitles files of the release by the episode
// of their video on the left, the folders on the right — the name of a folder is the name of the voice-over or of the
// subtitles in the rows of the series. A pick let go on a folder is placed in it by hand.
const props = defineProps<{ editor: Editor; tab: "voices" | "subtitles" }>()
const emit = defineEmits<{ failure: [string] }>()

type Shown = TrackFilter | "fonts"
const kind = computed(() => props.tab === "voices" ? "voice" as const : "subtitles" as const)
const FILTERS = computed<Shown[]>(() => props.tab === "subtitles" ? ["all", "unplaced", "placed", "fonts"] : ["all", "unplaced", "placed"])
const filterWord = (each: Shown): string => t(({ all: "torrent.filter.all", fonts: "torrent.filter.fonts",
  unplaced: props.tab === "voices" ? "torrent.filter.noVoice" : "torrent.filter.noFolder", placed: "torrent.filter.inFolder" } as const)[each])
const filter = ref<Shown>("all")
const entries = computed(() => props.editor.view.tabs[props.tab])
const fonts = computed(() => props.editor.view.tabs.files.filter((file) => file.kind === "font"))
const counts = computed(() => ({ ...trackCounts(entries.value), fonts: fonts.value.length }))
const groups = computed(() => filter.value === "fonts" ? []
  : byEpisode(entries.value.filter((entry) => trackFits(entry, filter.value as TrackFilter))))
// A row is painted by its state: red no folder or no language yet (it goes no further), green placed.
const stateOf = (entry: TrackEntry): string => entry.waiting ? "is-unplaced" : "is-placed"

const groupTitle = (group: (typeof groups.value)[number]): string => group.place
  ? t("torrent.tracks.group", { season: group.place.season, episode: group.place.last ? `${group.place.episode}–${group.place.last}` : group.place.episode })
  : t("torrent.tracks.noEpisode")
const groupSide = (group: (typeof groups.value)[number]): string => t("torrent.tracks.count",
  { n: group.entries.length, placed: group.entries.filter((each) => !each.waiting).length })

const pick = useTorrentPick()
const chosenEntries = computed(() => entries.value.filter((entry) => pick.has(entry.item)))
// «Выбрать все из каталога»: every file of the folder of the release the first item chosen lies in.
const pickedFolder = computed(() => chosenEntries.value.length ? folderOf(chosenEntries.value[0].path) : null)
const chooseFolder = (): void => pick.choose(entries.value.filter((entry) => folderOf(entry.path) === pickedFolder.value).map((entry) => entry.item))

const kinds = computed(() => [kind.value])
const anyFolder = computed(() => props.editor.view.folders.some((folder) => folder.kind === kind.value))

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
const back = () => send(() => unassign(url.value, props.tab, chosenEntries.value.filter((each) => each.by === "manual").map((each) => each.item)))
// «Авто»: the automatic layout makes again the folders of this kind the user deleted; the hand's placements stay.
const hiddenHere = computed(() => props.editor.view.hidden.some((each) => each.kind === kind.value))
const toAuto = () => send(() => autoLayout(url.value, [kind.value]))

const naming = ref(false)
const name = ref("")
async function create(): Promise<void> {
  const value = name.value.trim()
  if (!value) return
  busy.value = true
  try {
    const answer = await setFolder({ url: url.value, kind: kind.value, name: value })
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
  <div class="torrent-cols torrent-tracks">
    <section class="torrent-pane">
      <div class="torrent-pane-head">
        <b>{{ t(tab === "voices" ? "torrent.voices.title" : "torrent.subtitles.title") }}</b>
        <span class="torrent-pane-hint" :title="t('torrent.tracks.line')">{{ t("torrent.tracks.line") }}</span>
      </div>
      <div class="torrent-filters" role="group">
        <button v-for="each in FILTERS" :key="each" type="button" class="torrent-filter" :class="{ active: filter === each }"
                :aria-pressed="filter === each" @click="filter = each">
          {{ filterWord(each) }} <b>{{ counts[each] }}</b>
        </button>
      </div>
      <div class="torrent-pane-body">
        <template v-for="group in groups" :key="groupTitle(group)">
          <div class="torrent-row is-group">
            <input type="checkbox" :checked="pick.allOf(group.entries.map((each) => each.item))"
                   :indeterminate="pick.someOf(group.entries.map((each) => each.item))" :aria-label="groupTitle(group)"
                   @change="pick.toggleAll(group.entries.map((each) => each.item))">
            <span class="torrent-name">{{ groupTitle(group) }}</span><span class="torrent-side">{{ groupSide(group) }}</span>
          </div>
          <div v-for="entry in group.entries" :key="entry.item" class="torrent-row is-nested track-row"
               :class="[stateOf(entry), { 'is-chosen': pick.has(entry.item) }]">
            <span class="torrent-grip" draggable="true" :title="t('torrent.drag')" aria-hidden="true"
                  @dragstart="pick.dragStart($event, [entry.item])" @dragend="pick.dragEnd"><i class="pi pi-ellipsis-v" /></span>
            <input type="checkbox" :checked="pick.has(entry.item)" :aria-label="entry.path" @change="pick.toggle(entry.item)">
            <TorrentLine :line="entry.line" />
          </div>
        </template>
        <template v-if="filter === 'fonts' || (filter === 'all' && tab === 'subtitles' && fonts.length)">
          <div class="torrent-row is-group"><span class="torrent-name">{{ t("torrent.filter.fonts") }}</span>
            <span class="torrent-side">{{ t("torrent.tracks.fonts", { n: fonts.length }) }}</span></div>
          <template v-if="filter === 'fonts'">
            <div v-for="font in fonts" :key="font.item" class="torrent-row is-nested">
              <span class="torrent-name">{{ font.path }}</span>
            </div>
          </template>
        </template>
        <p v-if="!entries.length" class="torrent-note">{{ t("torrent.empty") }}</p>
      </div>
      <div v-if="pick.count.value" class="torrent-pick">
        {{ t("torrent.select.count", { n: pick.count.value }) }}
        <button v-if="pickedFolder !== null" type="button" class="torrent-mini do-choose-folder" @click="chooseFolder">
          {{ t("torrent.select.folder", { folder: pickedFolder || t("torrent.root") }) }}</button>
        <div class="torrent-pick-tools">
          <button v-if="chosenEntries.some((each) => each.by === 'manual')" type="button" class="torrent-mini" :disabled="busy" @click="back">
            {{ t("torrent.unassign") }}</button>
          <button type="button" class="torrent-mini" @click="pick.clear">{{ t("torrent.select.clear") }}</button>
        </div>
      </div>
    </section>

    <section class="torrent-pane">
      <div class="torrent-pane-head">
        <b>{{ t(tab === "voices" ? "torrent.folders.voices" : "torrent.folders.subtitles") }}</b>
        <span class="torrent-pane-hint" :title="t(tab === 'voices' ? 'torrent.folders.voicesHint' : 'torrent.folders.subtitlesHint')">{{ t(tab === "voices" ? "torrent.folders.voicesHint" : "torrent.folders.subtitlesHint") }}</span>
        <div class="torrent-pane-tools">
          <button type="button" class="torrent-mini do-auto" :disabled="busy || !hiddenHere" :title="t('torrent.autoHint')"
                  @click="toAuto">{{ t("torrent.auto") }}</button>
          <button type="button" class="torrent-mini is-primary do-add-folder" @click="naming = true">
            + {{ t(tab === "voices" ? "torrent.folder.addVoice" : "torrent.folder.addSubtitles") }}</button>
        </div>
      </div>
      <form v-if="naming" class="torrent-new" @submit.prevent="create">
        <label>{{ t("torrent.folder.name") }} <input v-model="name" class="p-inputtext" required></label>
        <button type="submit" class="torrent-mini is-primary" :disabled="busy">{{ t("torrent.folder.create") }}</button>
        <button type="button" class="torrent-mini" @click="naming = false">{{ t("common.cancel") }}</button>
      </form>
      <div class="torrent-pane-body">
        <TorrentFolders :editor="editor" :kinds="kinds" :pick="pick" :fits="() => true"
                        @drop="(_, name, items) => toFolder(name, items)" @failure="emit('failure', $event)" />
        <p v-if="!anyFolder" class="torrent-note">{{ t("torrent.folders.none") }}</p>
      </div>
    </section>
  </div>
</template>
