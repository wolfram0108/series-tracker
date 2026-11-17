<script setup lang="ts">
import { computed, ref } from "vue"
import { hasMessage, t } from "../../../i18n"
import { LANGUAGES } from "../../../lib/languages"
import { SUBTITLE_TYPES } from "../../../lib/ruleVocabulary"
import { FROM_META, episodeFolder, folderRows, nameOf, ruleOf, type Editor, type Folder, type FolderRow, type FolderTrack } from "../../../lib/torrent"
import { spansOf } from "../../../lib/ranges"
import { assign, deleteFolder, setFolder, unassign } from "../../../composables/torrentCalls"
import type { Answer } from "../../../composables/sourceCalls"
import type { TorrentPick } from "../../../composables/useTorrentPick"

// The folders of the tracks as one table per kind (the boards «Озвучки», «Субтитры», «Контейнеры»): a row is a folder —
// its name is the name of the voice-over or of the subtitles in the rows of the series, its language and type of subtitles
// are set in it, where it comes from (the titles in the files, a rule, a hand) is shown; unfolded, it lists every track
// it holds: its episode, its stream as the left pane names it (A:n), «вручную» or «авто» (a rule, the file). A pick let go on
// a row is placed in that folder by hand; the bin takes a track out of the folder, or the folder with every track of it.
const props = defineProps<{
  editor: Editor
  kinds: readonly Folder["kind"][]
  pick: TorrentPick
  /** The pick fits a folder of the kind (a sound in a folder of a voice-over, subtitles in one of subtitles). */
  fits: (kind: Folder["kind"]) => boolean
}>()
const emit = defineEmits<{ drop: [Folder["kind"], string, string[]]; failure: [string] }>()

const TYPES = SUBTITLE_TYPES
const groups = computed(() => props.kinds.map((kind) => ({ kind, rows: folderRows(props.editor.view, kind) })))
const languages = computed(() => [...new Set([...props.editor.view.tabs.voices, ...props.editor.view.tabs.subtitles,
  ...props.editor.view.tabs.streams].map((entry) => entry.language.trim().toLowerCase()).filter((code) => code && code !== "und"))].sort())
const live = (kind: Folder["kind"]): boolean => props.pick.count.value > 0 && props.fits(kind)

const keyOf = (row: FolderRow): string => `${row.kind}:${row.name}`
const open = ref(new Set<string>())
function flipped(set: Set<string>, key: string): Set<string> {
  const next = new Set(set)
  if (!next.delete(key)) next.add(key)
  return next
}
const unfold = (row: FolderRow): void => { open.value = flipped(open.value, keyOf(row)) }

// What its tracks go out with: its own language set by hand, else the one its tracks carry (a rule's or the file's).
const languageOf = (row: FolderRow): string | null => row.language || (row.noLanguage ? "" : row.trackLanguage) || null
// The languages of the project (those the constructor offers), those the tracks of the release carry, the folder's own.
const optionsOf = (row: FolderRow): string[] => [...new Set([...LANGUAGES, ...languages.value, languageOf(row) ?? ""].filter(Boolean))]
const languageName = (code: string): string => { const key = `voices.language.${code}`; return hasMessage(key) ? t(key) : code }
const episodesOf = (row: FolderRow): string => {
  const places = row.tracks.flatMap((each) => each.entry.place ? [each.entry.place] : [])
  if (!places.length) return "—"
  return new Set(places.map((place) => place.season)).size === 1 ? spansOf(places.map((place) => place.episode)) : row.episodes
}
// Where a track got its folder: a hand, the title in its file, or a rule (with its row).
type Origin = { css: string; text: string }
const HAND: Origin = { css: "torrent-hand", text: t("torrent.by.manual") }
function originOf(by: string | null): Origin {
  if (by === "manual") return HAND
  if (by === FROM_META) return { css: "torrent-meta-folder", text: t("torrent.folder.fromFile") }
  const rule = ruleOf(by)
  return rule === null ? HAND : { css: "torrent-rule",
    text: rule.row === null ? t("torrent.by.rule", { rule: rule.rule }) : t("torrent.by.row", { rule: rule.rule, row: rule.row }) }
}
// Where a folder's tracks come from, each way once: a folder of a file and a hand says both. An empty one was made by hand.
const sourcesOf = (row: FolderRow): Origin[] => row.tracks.length
  ? [...new Map(row.tracks.map((each) => originOf(each.entry.by)).map((each) => [each.text, each] as const)).values()]
  : [HAND]
// What tells the track among the lines of the left pane: the stream of a container (`A:4`), the name of a file of its own.
function idOf(track: FolderTrack): string {
  const parts = track.entry.line.split(" | ")
  return track.tab === "streams" ? parts.find((part) => /^A:/.test(part)) ?? "" : parts.find((part) => !/^[A-Z]{1,3}:/.test(part)) ?? ""
}
const placeOf = (track: FolderTrack): string => {
  const place = track.entry.place
  return place ? episodeFolder(place.season, place.episode, place.last) : t("torrent.tracks.noEpisode")
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
  url: url.value, kind: row.kind, name: row.name, language: change.language ?? row.language,
  subtitle_type: (change.subtitle_type ?? row.subtitle_type) as "" | (typeof TYPES)[number] })])
// The bin of a track: one a hand put here goes back to where the rules and its file put it; one of a rule or of its file
// would come back, so it is left out of the output (as «Не брать» on the left).
const dropTrack = (track: FolderTrack) => run([track.entry.by === "manual"
  ? () => unassign(url.value, track.tab, [track.entry.item])
  : () => assign({ url: url.value, tab: track.tab, items: [track.entry.item], excluded: true })])
// The bin of a folder: its tracks go out of the output, in each tab they lie in, and a folder the user named is forgotten.
function dropFolder(row: FolderRow): Promise<void> {
  const byTab = new Map<FolderTrack["tab"], string[]>()
  for (const each of row.tracks) byTab.set(each.tab, [...byTab.get(each.tab) ?? [], each.entry.item])
  return run([...[...byTab].map(([tab, items]) => () => assign({ url: url.value, tab, items, excluded: true })),
    ...(row.manual ? [() => deleteFolder(url.value, row.kind, row.name)] : [])])
}
const target = (row: FolderRow) => live(row.kind) ? props.pick.target((items) => emit("drop", row.kind, row.name, items)) : {}
</script>

<template>
  <div class="torrent-folders">
    <template v-for="group in groups" :key="group.kind">
      <div class="torrent-folders-group">{{ t(group.kind === "voice" ? "torrent.folders.groupVoices" : "torrent.folders.groupSubtitles") }}
        <span>{{ group.rows.length }}</span></div>
      <table v-if="group.rows.length" class="torrent-folders-table">
        <colgroup>
          <col class="torrent-col-fold"><col><col class="torrent-col-language">
          <col v-if="group.kind === 'subtitles'" class="torrent-col-type">
          <col class="torrent-col-source"><col class="torrent-col-episodes">
          <col :class="group.kind === 'subtitles' ? 'torrent-col-tracks-short' : 'torrent-col-tracks'"><col class="torrent-col-act">
        </colgroup>
        <thead>
          <tr>
            <td />
            <th scope="col">{{ t("torrent.folders.col.name") }}</th>
            <th scope="col">{{ t("torrent.folders.col.language") }}</th>
            <th v-if="group.kind === 'subtitles'" scope="col">{{ t("torrent.folders.col.type") }}</th>
            <th scope="col">{{ t("torrent.folders.col.source") }}</th>
            <th scope="col">{{ t("torrent.folders.col.episodes") }}</th>
            <th scope="col">{{ t(group.kind === "subtitles" ? "torrent.folders.col.tracksShort" : "torrent.folders.col.tracks") }}</th>
            <td />
          </tr>
        </thead>
        <tbody>
          <template v-for="row in group.rows" :key="keyOf(row)">
            <tr class="torrent-folder" :data-folder="row.name" :class="{ 'is-open': open.has(keyOf(row)), 'is-drop': live(row.kind) }"
                v-bind="target(row)">
              <td class="torrent-folders-fold">
                <button v-if="row.tracks.length || row.fonts.length" type="button" class="torrent-folders-chev"
                        :aria-expanded="open.has(keyOf(row))" :aria-label="t('torrent.folders.unfold', { name: row.name })"
                        @click="unfold(row)">{{ open.has(keyOf(row)) ? "▾" : "▸" }}</button>
              </td>
              <td class="torrent-folder-name" :title="row.name">{{ row.name }}</td>
              <td class="torrent-folders-lang">
                <span class="torrent-language-box"><select class="torrent-language" :class="{ 'is-bad': !languageOf(row) }" :value="languageOf(row) ?? ''" :disabled="busy"
                        :aria-label="t('torrent.folders.languageOf', { name: row.name })"
                        @change="save(row, { language: ($event.target as HTMLSelectElement).value })">
                  <option v-if="!languageOf(row)" value="" disabled>
                    {{ row.noLanguage ? t("torrent.folders.noLanguage", { n: row.noLanguage }) : t("torrent.folder.language") }}</option>
                  <option v-else-if="row.language" value="">{{ t("torrent.folders.trackLanguage") }}</option>
                  <option v-for="code in optionsOf(row)" :key="code" :value="code" :title="languageName(code)">{{ code }}</option>
                </select></span>
              </td>
              <td v-if="group.kind === 'subtitles'">
                <span class="torrent-types" role="group" :aria-label="t('torrent.folders.col.type')">
                  <button v-for="each in TYPES" :key="each" type="button" :class="{ active: row.subtitle_type === each }"
                          :aria-pressed="row.subtitle_type === each" :title="t(`torrent.type.${each}`)" :disabled="busy"
                          @click="save(row, { subtitle_type: row.subtitle_type === each ? '' : each })">{{ t(`torrent.type.short.${each}`) }}</button>
                </span>
              </td>
              <td><span class="torrent-folders-sources">
                <span v-for="each in sourcesOf(row)" :key="each.text" :class="each.css" :title="each.text">{{ each.text }}</span></span></td>
              <td class="torrent-folders-num" :title="episodesOf(row)">{{ episodesOf(row) }}</td>
              <td class="torrent-folders-num">{{ row.tracks.length }}</td>
              <td class="torrent-folders-act">
                <button v-if="live(row.kind)" type="button" class="torrent-put drop-folder" :disabled="busy"
                        :title="t('torrent.folder.drop', { name: row.name, n: pick.count.value })"
                        @click="emit('drop', row.kind, row.name, [...pick.chosen.value])">{{ t("torrent.folders.put", { n: pick.count.value }) }}</button>
                <button v-else-if="!pick.count.value" type="button" class="torrent-bin-btn do-delete-folder" :disabled="busy"
                        :title="t('torrent.folders.drop', { name: row.name })" :aria-label="t('torrent.folders.drop', { name: row.name })"
                        @click="dropFolder(row)"><i class="pi pi-trash" aria-hidden="true" /></button>
              </td>
            </tr>
            <template v-if="open.has(keyOf(row))">
              <tr v-for="track in row.tracks" :key="track.entry.item" class="torrent-folder-track">
                <td />
                <td class="torrent-folders-place">{{ placeOf(track) }}</td>
                <td class="torrent-folders-id" :colspan="group.kind === 'subtitles' ? 2 : 1" :title="idOf(track)">{{ idOf(track) }}</td>
                <td colspan="3"><span v-if="track.entry.by === 'manual'" class="torrent-hand">{{ t("torrent.by.manual") }}</span>
                  <span v-else class="torrent-auto">{{ t("torrent.by.auto") }}</span></td>
                <td class="torrent-folders-act">
                  <button type="button" class="torrent-bin-btn do-drop-track" :disabled="busy"
                          :title="t(track.entry.by === 'manual' ? 'torrent.folders.dropHand' : 'torrent.folders.dropTrack')"
                          :aria-label="t(track.entry.by === 'manual' ? 'torrent.folders.dropHand' : 'torrent.folders.dropTrack')"
                          @click="dropTrack(track)"><i class="pi pi-trash" aria-hidden="true" /></button>
                </td>
              </tr>
              <tr v-if="row.fonts.length" class="torrent-folder-track">
                <td />
                <td class="torrent-folders-place">{{ t("torrent.folder.fonts", { n: row.fonts.length }) }}</td>
                <td class="torrent-folders-file" :colspan="group.kind === 'subtitles' ? 6 : 5" :title="row.fonts.join('\n')">
                  {{ row.fonts.map(nameOf).join(", ") }}</td>
              </tr>
            </template>
          </template>
        </tbody>
      </table>
    </template>
  </div>
</template>
