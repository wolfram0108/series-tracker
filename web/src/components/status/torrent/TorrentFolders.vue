<script setup lang="ts">
import { computed, ref } from "vue"
import { hasMessage, t, type MessageKey } from "../../../i18n"
import { LANGUAGES } from "../../../lib/languages"
import { SUBTITLE_TYPES } from "../../../lib/ruleVocabulary"
import { FROM_META, episodeFolder, folderRows, kindOfTab, nameOf, ruleOf, type Editor, type Folder, type FolderRow, type FolderTrack,
  type TrackTab } from "../../../lib/torrent"
import { spansOf } from "../../../lib/ranges"
import { deleteFolder, removeFromFolder, setFolder } from "../../../composables/torrentCalls"
import type { Answer } from "../../../composables/sourceCalls"
import type { TorrentPick } from "../../../composables/useTorrentPick"
import SubtitleTypes from "../../SubtitleTypes.vue"
import NumberMark from "../../NumberMark.vue"

// The folders of the tracks as one table per kind (the boards «Озвучки», «Субтитры», «Контейнеры»): a row is a folder —
// its name is the name of the voice-over or of the subtitles in the rows of the series, its language and type of subtitles
// are set in it, where it comes from (the titles in the files, a rule, a hand) is shown; unfolded, it lists every track
// it holds: its episode, its stream as the left pane names it (A:n), «вручную» or «авто» (a rule, the file). A pick let go on
// a row is placed in that folder by hand. Any folder may be deleted: its tracks are in no folder (red) — the hand's go
// back to the rules and the files — and one of the automatic layout is not made again until «Авто». A track a hand put
// is taken out of it by its «−» — red. Nothing is left out here.
const props = defineProps<{
  editor: Editor
  /** The tab the folders are of: each tab of tracks has its own set. */
  tab: TrackTab
  pick: TorrentPick
  /** The pick fits a folder of the kind (a sound in a folder of a voice-over, subtitles in one of subtitles). */
  fits: (kind: Folder["kind"]) => boolean
}>()
const emit = defineEmits<{ drop: [Folder["kind"], string, string[]]; failure: [string] }>()

const TYPES = SUBTITLE_TYPES
const groups = computed(() => [{ kind: kindOfTab(props.tab), rows: folderRows(props.editor.view, props.tab) }])
const languages = computed(() => [...new Set([...props.editor.view.tabs.voices, ...props.editor.view.tabs.subtitles,
  ...props.editor.view.tabs.voice_streams, ...props.editor.view.tabs.subtitle_streams].map((entry) => entry.language.trim().toLowerCase()).filter((code) => code && code !== "und"))].sort())
const live = (kind: Folder["kind"]): boolean => props.pick.count.value > 0 && props.fits(kind)

const keyOf = (row: FolderRow): string => `${row.tab}:${row.name}`
const open = ref(new Set<string>())
function flipped(set: Set<string>, key: string): Set<string> {
  const next = new Set(set)
  if (!next.delete(key)) next.add(key)
  return next
}
const unfold = (row: FolderRow): void => { open.value = flipped(open.value, keyOf(row)) }

// What its tracks go out with: its own language set by hand, else the one its tracks carry (a rule's or the file's).
const languageOf = (row: FolderRow): string | null => row.language || row.trackLanguage || null
// The languages of the project (those the constructor offers), those the tracks of the release carry, the folder's own.
const optionsOf = (row: FolderRow): string[] => [...new Set([...LANGUAGES, ...languages.value, languageOf(row) ?? ""].filter(Boolean))]
const languageName = (code: string): string => { const key = `voices.language.${code}`; return hasMessage(key) ? t(key) : code }
const episodesOf = (row: FolderRow): string => {
  const places = row.tracks.flatMap((each) => each.entry.place ? [each.entry.place] : [])
  if (!places.length) return "—"
  return new Set(places.map((place) => place.season)).size === 1 ? spansOf(places.map((place) => place.episode)) : row.episodes
}
// Where a track got its folder: a hand, the title in its file, or a rule — by its number in the list of the rules and its
// colour (NumberMark), its name and row in the title.
type Origin = { css: string; text: string; number?: number }
const HAND: Origin = { css: "torrent-hand", text: t("torrent.by.manual") }
function originOf(by: string | null, number?: number | null): Origin {
  if (by === "manual") return HAND
  if (by === FROM_META) return { css: "torrent-meta-folder", text: t("torrent.folder.fromFile") }
  const rule = ruleOf(by)
  if (rule === null) return HAND
  const text = rule.row === null ? t("torrent.by.rule", { rule: rule.rule }) : t("torrent.by.row", { rule: rule.rule, row: rule.row })
  return { css: "torrent-rule", text, ...(number ? { number } : {}) }
}
// Where the folder itself comes from — not each of its tracks (those say it on their rows): the titles in the files, when
// any of its tracks is named by them; else the rule that names it; else the hand (an empty folder the user made too).
function folderOrigin(row: FolderRow): Origin {
  const ways = row.tracks.map((each) => each.entry.by)
  if (ways.includes(FROM_META)) return originOf(FROM_META)
  const ruled = row.tracks.find((each) => each.entry.by !== "manual" && ruleOf(each.entry.by) !== null)
  return ruled ? originOf(ruled.entry.by, ruled.entry.rule_number) : HAND
}
// Where a track got its folder, as its row says it: a hand, a rule, or «авто» — the title in its file.
function trackOrigin(entry: { by: string | null; rule_number?: number | null }): Origin {
  if (entry.by === FROM_META) return { css: "torrent-auto", text: t("torrent.by.auto") }
  return originOf(entry.by, entry.rule_number)
}
// What tells the track among the lines of the left pane: the stream of a container (`A:4`), the name of a file of its own.
function idOf(track: FolderTrack): string {
  const parts = track.entry.line.split(" | ")
  return track.tab === "voice_streams" || track.tab === "subtitle_streams" ? parts.find((part) => /^A:/.test(part)) ?? "" : parts.find((part) => !/^[A-Z]{1,3}:/.test(part)) ?? ""
}
const placeOf = (track: FolderTrack): string => {
  const place = track.entry.place
  return place ? episodeFolder(place.season, place.episode, place.last, place.tag) : t("torrent.tracks.noEpisode")
}

const busy = ref(false)
async function run(calls: (() => Promise<Answer<unknown>>)[]): Promise<void> {
  busy.value = true
  try {
    for (const call of calls) {
      const answer = await call()
      if (!answer.ok) {
        emit("failure", answer.text)
        return
      }
    }
  } finally {
    busy.value = false
  }
}
const url = computed(() => props.editor.url)
const save = (row: FolderRow, change: { language?: string; subtitle_type?: "" | (typeof TYPES)[number] }) => run([() => setFolder({
  url: url.value, tab: row.tab, name: row.name, language: change.language ?? row.language,
  subtitle_type: (change.subtitle_type ?? row.subtitle_type) as "" | (typeof TYPES)[number] })])
// «−» of a track: out of the folder, red, whoever put it there; «Авто» gives the automatic layout back.
const dropTrack = (track: FolderTrack) => run([() => removeFromFolder(url.value, track.tab, [track.entry.item])])
// A folder deleted: the hand's placements in it go with it, the automatic layout does not make it again until «Авто».
function dropFolder(row: FolderRow): Promise<void> {
  const items = row.tracks.filter((each) => each.entry.by === "manual").map((each) => each.entry.item)
  const automatic = row.tracks.some((each) => each.entry.by !== "manual")
  return run([() => deleteFolder(url.value, row.tab, row.name, items, automatic)])
}
const target = (row: FolderRow) => live(row.kind) ? props.pick.target((items) => emit("drop", row.kind, row.name, items)) : {}
</script>

<template>
  <div class="torrent-folders">
    <template v-for="group in groups" :key="group.kind">
      <div class="torrent-folders-group">{{ t(group.kind === "voice" ? "torrent.folders.groupVoices" : "torrent.folders.groupSubtitles") }}
        <span>{{ group.rows.length }}</span></div>
      <!-- Four columns: the episode (no title in the head), what the track is, its type (subtitles), its source. Every folder
           is a head of its own over its tracks: its name, language, episodes and number of tracks in one line, its type and
           source in their columns. -->
      <table v-if="group.rows.length" class="torrent-folders-table" :class="`is-${group.kind}`">
        <colgroup>
          <col class="torrent-col-episode"><col>
          <col v-if="group.kind === 'subtitles'" class="torrent-col-type">
          <col class="torrent-col-source"><col class="torrent-col-act">
        </colgroup>
        <thead>
          <tr>
            <td />
            <th scope="col">{{ t("torrent.folders.col.name") }}</th>
            <th v-if="group.kind === 'subtitles'" scope="col">{{ t("torrent.folders.col.type") }}</th>
            <th scope="col">{{ t("torrent.folders.col.source") }}</th>
            <td />
          </tr>
        </thead>
        <tbody>
          <template v-for="row in group.rows" :key="keyOf(row)">
            <tr class="torrent-folder" :data-folder="row.name" :class="{ 'is-open': open.has(keyOf(row)), 'is-drop': live(row.kind) }"
                v-bind="target(row)">
              <td colspan="2" class="torrent-folder-head">
                <span class="torrent-folder-line">
                  <button v-if="row.tracks.length || row.fonts.length" type="button" class="torrent-folders-chev"
                          :aria-expanded="open.has(keyOf(row))" :aria-label="t('torrent.folders.unfold', { name: row.name })"
                          @click="unfold(row)">{{ open.has(keyOf(row)) ? "▾" : "▸" }}</button>
                  <span v-else class="torrent-folders-gap" aria-hidden="true" />
                  <span class="torrent-folder-name" :title="row.name">{{ row.name }}</span>
                  <span class="torrent-language-box"><select class="torrent-language" :value="languageOf(row) ?? ''" :disabled="busy"
                          :aria-label="t('torrent.folders.languageOf', { name: row.name })"
                          @change="save(row, { language: ($event.target as HTMLSelectElement).value })">
                    <option v-if="!languageOf(row)" value="" disabled>
                      {{ t("torrent.folder.language") }}</option>
                    <option v-else-if="row.language" value="">{{ t("torrent.folders.trackLanguage") }}</option>
                    <option v-for="code in optionsOf(row)" :key="code" :value="code" :title="languageName(code)">{{ code }}</option>
                  </select></span>
                  <span class="torrent-folder-fact" :title="t('torrent.folders.col.episodes')">{{ episodesOf(row) }}</span>
                  <span class="torrent-folder-fact" :title="t('torrent.folders.col.tracks')">{{ t("torrent.folders.tracks", { n: row.tracks.length }) }}</span>
                </span>
              </td>
              <td v-if="group.kind === 'subtitles'">
                <SubtitleTypes :model-value="row.subtitle_type" :disabled="busy" :label="t('torrent.folders.col.type')"
                               @update:model-value="save(row, { subtitle_type: $event })" />
              </td>
              <td class="torrent-folders-origin">
                <NumberMark v-if="folderOrigin(row).number" :n="folderOrigin(row).number!" :title="folderOrigin(row).text" />
                <span v-else :class="folderOrigin(row).css" :title="folderOrigin(row).text">{{ folderOrigin(row).text }}</span></td>
              <td class="torrent-folders-act">
                <button v-if="live(row.kind)" type="button" class="torrent-put drop-folder" :disabled="busy"
                        :title="t('torrent.folder.drop', { name: row.name, n: pick.count.value })"
                        @click="emit('drop', row.kind, row.name, [...pick.chosen.value])">{{ t("torrent.folders.put", { n: pick.count.value }) }}</button>
                <button v-else-if="!pick.count.value" type="button" class="torrent-bin-btn do-delete-folder" :disabled="busy"
                        :title="t('torrent.folders.drop', { name: row.name })" :aria-label="t('torrent.folders.drop', { name: row.name })"
                        @click="dropFolder(row)"><i class="pi pi-times" aria-hidden="true" /></button>
              </td>
            </tr>
            <template v-if="open.has(keyOf(row))">
              <tr v-for="track in row.tracks" :key="track.entry.item" class="torrent-folder-track">
                <td class="torrent-folders-place">{{ placeOf(track) }}</td>
                <td class="torrent-folders-id" :title="idOf(track)">{{ idOf(track) }}</td>
                <td v-if="group.kind === 'subtitles'">
                  <span v-if="track.entry.subtitle_type" class="torrent-type-mark" :title="t(`torrent.type.${track.entry.subtitle_type}` as MessageKey)">
                    {{ t(`torrent.type.short.${track.entry.subtitle_type}` as MessageKey) }}</span>
                  <span v-else class="torrent-type-none" :title="t('torrent.folders.noType')">—</span>
                </td>
                <td class="torrent-folders-origin">
                  <NumberMark v-if="trackOrigin(track.entry).number" :n="trackOrigin(track.entry).number!" :title="trackOrigin(track.entry).text" />
                  <span v-else :class="trackOrigin(track.entry).css" :title="trackOrigin(track.entry).text">{{ trackOrigin(track.entry).text }}</span></td>
                <td class="torrent-folders-act">
                  <button type="button" class="torrent-bin-btn do-drop-track" :disabled="busy"
                          :title="t('torrent.folders.dropTrack')" :aria-label="t('torrent.folders.dropTrack')"
                          @click="dropTrack(track)"><i class="pi pi-minus" aria-hidden="true" /></button>
                </td>
              </tr>
              <tr v-if="row.fonts.length" class="torrent-folder-track">
                <td class="torrent-folders-place">{{ t("torrent.folder.fonts", { n: row.fonts.length }) }}</td>
                <td class="torrent-folders-file" :colspan="group.kind === 'subtitles' ? 4 : 3" :title="row.fonts.join('\n')">
                  {{ row.fonts.map(nameOf).join(", ") }}</td>
              </tr>
            </template>
          </template>
        </tbody>
      </table>
    </template>
  </div>
</template>
