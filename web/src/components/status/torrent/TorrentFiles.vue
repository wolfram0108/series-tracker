<script setup lang="ts">
import { computed, ref } from "vue"
import { t, tn, type MessageKey } from "../../../i18n"
import { spansOf } from "../../../lib/ranges"
import { byFolder, draftOf, episodeFolder, fileCounts, fileFilterOf, nameOf, placeKey, ruleOf, type DraftEpisode, type Editor,
  type FileConflict, type FileEntry, type FileFilter, type FileKind, type ReleaseView } from "../../../lib/torrent"
import { assign, unassign } from "../../../composables/torrentCalls"
import { useTorrentPick } from "../../../composables/useTorrentPick"
import TorrentLine from "./TorrentLine.vue"
import TorrentPanes from "./TorrentPanes.vue"
import NumberMark from "../../NumberMark.vue"
import type { Answer } from "../../../composables/sourceCalls"

// The editor «Файлы» (the board «Файлы»): the files of the release by their folder on the left, the draft on the right —
// seasons, their episodes and the files laid in each by the rules of the tab or by hand. A pick is let go on a season (its
// files take the episodes after its last one in the order of their names) or on an episode. A row is painted by its state:
// green placed, red not (it goes no further), plain a file that takes no part.
const props = defineProps<{ editor: Editor; release?: ReleaseView }>()
const emit = defineEmits<{ failure: [string] }>()

const FILTERS: readonly FileFilter[] = ["all", "unplaced", "placed", "notInvolved"]
const entries = computed(() => props.editor.view.tabs.files)
const counts = computed(() => fileCounts(entries.value))
// Until the user picks a filter the list shows what waits for a place, or every file when nothing does.
const chosenFilter = ref<FileFilter | null>(null)
const filter = computed<FileFilter>(() => chosenFilter.value ?? (counts.value.unplaced ? "unplaced" : "all"))
const fits = (entry: FileEntry): boolean => filter.value === "all" || fileFilterOf(entry) === filter.value
const folders = computed(() => byFolder(entries.value).map((group) => {
  const shown = group.entries.filter(fits)
  return { ...group, shown, kinds: fileCounts(group.entries) }
}))
const visible = computed(() => folders.value.filter((group) => group.shown.length))
const hidden = computed(() => filter.value === "all" ? [] : folders.value.filter((group) => !group.shown.length))
const folded = ref(new Set<string>())
function fold(folder: string): void {
  const next = new Set(folded.value)
  if (!next.delete(folder)) next.add(folder)
  folded.value = next
}

// How far the client has downloaded each file, as the release tells it.
const fetched = computed(() => new Map((props.release?.files ?? []).map((file) => [file.path, file] as const)))
function loadOf(entry: FileEntry): string {
  const file = fetched.value.get(entry.path)
  if (!file) return ""
  if (file.priority === 0) return t("torrent.file.skipped")
  return file.progress < 1 ? t("torrent.file.progress", { share: Math.floor(file.progress * 100) }) : ""
}

const pick = useTorrentPick()
const chosenEntries = computed(() => entries.value.filter((entry) => pick.has(entry.item)))
const folderLabel = (folder: string): string => folder || t("torrent.root")

// The draft: what the core laid, and the seasons and episodes the user opened by hand to lay files in.
const draft = computed(() => draftOf(entries.value))
const extraSeasons = ref<number[]>([])
// An episode opened by hand: its number and the tag of its version (empty for the plain one).
const extraEpisodes = ref<Record<number, { episode: number; tag: string }[]>>({})
const seasons = computed(() => {
  const laid = draft.value.map((season) => ({ ...season, laid: true }))
  const numbers = new Set(laid.map((season) => season.season))
  for (const season of extraSeasons.value) if (!numbers.has(season)) laid.push({ season, episodes: [], manual: true, laid: false })
  return laid.sort((a, b) => a.season - b.season).map((season) => {
    const have = new Set(season.episodes.map((episode) => `${episode.episode}:${episode.tag}`))
    const opened: DraftEpisode[] = (extraEpisodes.value[season.season] ?? []).filter((each) => !have.has(`${each.episode}:${each.tag}`))
      .map((each) => ({ episode: each.episode, last: null, tag: each.tag, files: [], kinds: {} }))
    const episodes = [...season.episodes, ...opened].sort((a, b) => a.episode - b.episode || a.tag.localeCompare(b.tag))
    return { ...season, episodes, next: Math.max(0, ...season.episodes.map((each) => each.last ?? each.episode)) + 1 }
  })
})
// An episode is a folder of the draft, named by its place (s01e01), with the files laid in it shown under it.
const foldedEpisodes = ref(new Set<string>())
const episodeKey = (season: number, episode: DraftEpisode): string =>
  placeKey({ season, episode: episode.episode, last: episode.last, tag: episode.tag })
function foldEpisode(key: string): void {
  const next = new Set(foldedEpisodes.value)
  if (!next.delete(key)) next.add(key)
  foldedEpisodes.value = next
}
const KINDS: readonly FileKind[] = ["video", "audio", "subtitles", "font", "other"]
const kindWord = (kind: FileKind): string => t(`torrent.kind.${kind}` as MessageKey)
function howOf(entry: FileEntry): string {
  if (entry.by === "manual") return t("torrent.by.manual")
  const rule = ruleOf(entry.by)
  if (!rule) return ""
  return rule.row === null ? t("torrent.by.rule", { rule: rule.rule }) : t("torrent.by.row", { rule: rule.rule, row: rule.row })
}
// Why a file is red though a source gave it an episode: the episode is taken by the source before it, or one source gave
// it two videos.
function conflictText(conflict: FileConflict): string {
  const by = conflict.by === "manual" ? t("torrent.by.manual") : howOf({ by: conflict.by } as FileEntry)
  return t(conflict.code === "taken" ? "torrent.conflict.taken" : "torrent.conflict.twice", { episode: conflict.episode, by })
}

const adding = ref<{ season: number | null } | null>(null)
const number = ref<number | null>(null)
const tag = ref("")
function startAdding(season: number | null): void {
  adding.value = { season }
  tag.value = ""
  number.value = season === null ? Math.max(0, ...seasons.value.map((each) => each.season)) + 1
    : seasons.value.find((each) => each.season === season)?.next ?? 1
}
function add(): void {
  const value = number.value
  if (adding.value === null || value === null || value < 0) return
  const season = adding.value.season
  if (season === null) extraSeasons.value = [...extraSeasons.value, value]
  else extraEpisodes.value = { ...extraEpisodes.value,
    [season]: [...(extraEpisodes.value[season] ?? []), { episode: value, tag: tag.value.trim() }] }
  adding.value = null
}
function dropSeason(season: number): void {
  extraSeasons.value = extraSeasons.value.filter((each) => each !== season)
}

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
const url = computed(() => props.editor.url)
// Dropped on a season: the files in the order of their names take the episodes after the last one of the season.
const toSeason = (season: number, from: number) => (items: string[]) => send(() => assign({ url: url.value, tab: "files",
  items: [...items].sort((a, b) => a.localeCompare(b, undefined, { numeric: true })), season, episode: from, in_order: true }))
const toEpisode = (season: number, episode: DraftEpisode) => (items: string[]) => send(() => assign({ url: url.value,
  tab: "files", items, season, episode: episode.episode, episode_last: episode.last, tag: episode.tag }))
const back = (items: string[]) => send(() => unassign(url.value, "files", items))
const by = (apply: (items: string[]) => unknown) => () => { if (pick.count.value) void apply([...pick.chosen.value]) }
</script>

<template>
  <TorrentPanes class="torrent-files">
    <template #left-head><b>{{ t("torrent.files.title") }}</b><span class="torrent-pane-hint" :title="t('torrent.files.line')">{{ t("torrent.files.line") }}</span></template>
    <template #left>
      <div class="torrent-filters" role="group">
        <button v-for="each in FILTERS" :key="each" type="button" class="torrent-filter" :class="{ active: filter === each }"
                :aria-pressed="filter === each" @click="chosenFilter = each">
          {{ t(`torrent.filter.${each}` as MessageKey) }}
          <b>{{ counts[each] }}</b>
        </button>
      </div>
      <div class="torrent-pane-body">
        <template v-for="group in visible" :key="group.folder">
          <div class="torrent-row is-group">
            <span class="torrent-grip" draggable="true" :title="t('torrent.drag')" aria-hidden="true"
                  @dragstart="pick.dragStart($event, group.shown.map((each) => each.item))" @dragend="pick.dragEnd"><i class="pi pi-ellipsis-v" /></span>
            <input type="checkbox" :checked="pick.allOf(group.shown.map((each) => each.item))"
                   :indeterminate="pick.someOf(group.shown.map((each) => each.item))"
                   :aria-label="t('torrent.select.folder', { folder: folderLabel(group.folder) })"
                   @change="pick.toggleAll(group.shown.map((each) => each.item))">
            <button type="button" class="torrent-fold" :aria-expanded="!folded.has(group.folder)" @click="fold(group.folder)">
              <i class="pi" :class="folded.has(group.folder) ? 'pi-chevron-right' : 'pi-chevron-down'" aria-hidden="true" />
            </button>
            <TorrentLine v-if="group.folder" :line="`D:${group.folder}`" />
            <span v-else class="torrent-name">{{ t("torrent.root") }}</span>
            <span class="torrent-side">
              <template v-for="kind in (['unplaced', 'placed'] as const)" :key="kind">
                <span v-if="group.kinds[kind]" class="torrent-count" :class="`is-${kind}`">{{ t(`torrent.filter.${kind}`) }} {{ group.kinds[kind] }}</span>
              </template>
            </span>
          </div>
          <template v-if="!folded.has(group.folder)">
            <div v-for="entry in group.shown" :key="entry.item" class="torrent-row is-nested file-row"
                 :class="[`is-${fileFilterOf(entry)}`, { 'is-chosen': pick.has(entry.item) }]">
              <span class="torrent-grip" draggable="true" :title="t('torrent.drag')" aria-hidden="true"
                  @dragstart="pick.dragStart($event, [entry.item])" @dragend="pick.dragEnd"><i class="pi pi-ellipsis-v" /></span>
              <input type="checkbox" :checked="pick.has(entry.item)" :aria-label="nameOf(entry.path)" @change="pick.toggle(entry.item)">
              <TorrentLine :line="entry.line" :skip="['D']" />
              <span v-if="entry.conflict" class="torrent-how file-conflict">{{ conflictText(entry.conflict) }}</span>
              <span v-if="loadOf(entry)" class="torrent-how file-load">{{ loadOf(entry) }}</span>
            </div>
          </template>
        </template>
        <p v-if="!visible.length" class="torrent-note">{{ t("torrent.empty") }}</p>
        <p v-if="hidden.length" class="torrent-note">
          {{ t("torrent.files.hidden", { filter: t(`torrent.filter.${filter}` as MessageKey),
                                         list: hidden.map((group) => `${folderLabel(group.folder)} (${group.entries.length})`).join(", ") }) }}
        </p>
      </div>
      <div v-if="pick.count.value" class="torrent-pick">
        {{ t("torrent.select.count", { n: pick.count.value }) }}
        <div class="torrent-pick-tools">
          <button v-if="chosenEntries.some((each) => each.by === 'manual')" type="button" class="torrent-mini" :disabled="busy"
                  @click="back(chosenEntries.filter((each) => each.by === 'manual').map((each) => each.item))">{{ t("torrent.unassign") }}</button>
          <button type="button" class="torrent-mini" @click="pick.clear">{{ t("torrent.select.clear") }}</button>
        </div>
      </div>
    </template>

    <template #right-head>
      <b>{{ t("torrent.draft.title") }}</b><span class="torrent-pane-hint" :title="t('torrent.draft.hint')">{{ t("torrent.draft.hint") }}</span>
      <div class="torrent-pane-tools">
        <button type="button" class="torrent-mini do-add-season" @click="startAdding(null)">+ {{ t("torrent.draft.addSeason") }}</button>
      </div>
    </template>
    <template #right>
      <form v-if="adding" class="torrent-new" @submit.prevent="add">
        <label>{{ t(adding.season === null ? "torrent.draft.newSeason" : "torrent.draft.newEpisode") }}
          <input v-model.number="number" type="number" min="0" class="p-inputtext torrent-language" required>
        </label>
        <label v-if="adding.season !== null">{{ t("torrent.draft.tag") }}
          <input v-model="tag" type="text" class="p-inputtext torrent-language do-episode-tag" :placeholder="t('torrent.draft.tagHint')">
        </label>
        <button type="submit" class="torrent-mini is-primary">{{ t("torrent.folder.create") }}</button>
        <button type="button" class="torrent-mini" @click="adding = null">{{ t("common.cancel") }}</button>
      </form>
      <div class="torrent-pane-body">
        <div v-for="season in seasons" :key="season.season" class="torrent-season">
          <div class="torrent-season-head">
            <b>{{ t("torrent.draft.season", { n: season.season }) }}</b>
            <span class="torrent-pane-hint">
              <template v-if="season.episodes.some((each) => each.files.length)">
                {{ t("torrent.draft.episodes", { range: spansOf(season.episodes.filter((each) => each.files.length).map((each) => each.episode)) }) }}
                · {{ t(season.manual ? "torrent.draft.byHand" : "torrent.draft.byRules") }}
              </template>
              <template v-else>{{ t("torrent.draft.manualEmpty") }}</template>
            </span>
            <span class="torrent-pane-tools">
              <button type="button" class="torrent-mini" @click="startAdding(season.season)">+ {{ t("torrent.draft.addEpisode") }}</button>
              <button v-if="!season.laid" type="button" class="torrent-mini" @click="dropSeason(season.season)">{{ t("common.delete") }}</button>
            </span>
          </div>
          <template v-for="episode in season.episodes" :key="episodeKey(season.season, episode)">
            <div class="torrent-episode episode-row" v-bind="pick.target(toEpisode(season.season, episode))">
              <button type="button" class="torrent-fold" :aria-expanded="!foldedEpisodes.has(episodeKey(season.season, episode))"
                      :disabled="!episode.files.length" @click="foldEpisode(episodeKey(season.season, episode))">
                <i class="pi" :class="foldedEpisodes.has(episodeKey(season.season, episode)) ? 'pi-chevron-right' : 'pi-chevron-down'" aria-hidden="true" />
              </button>
              <i class="pi pi-folder torrent-folder-icon" aria-hidden="true" />
              <span class="torrent-name torrent-episode-name">{{ episodeFolder(season.season, episode.episode, episode.last, episode.tag) }}</span>
              <span v-if="!episode.files.length" class="torrent-how">{{ t("torrent.draft.empty") }}</span>
              <template v-for="kind in KINDS" :key="kind">
                <span v-if="episode.kinds[kind]" class="torrent-kind" :class="`is-${kind}`">{{ kindWord(kind) }} {{ episode.kinds[kind] }}</span>
              </template>
              <button v-if="pick.count.value" type="button" class="torrent-mini is-primary do-drop-episode" :disabled="busy"
                      :aria-label="t('torrent.draft.dropEpisode', { episode: episode.episode, n: pick.count.value })"
                      @click="by(toEpisode(season.season, episode))()"><i class="pi pi-arrow-down" aria-hidden="true" /></button>
            </div>
            <template v-if="!foldedEpisodes.has(episodeKey(season.season, episode))">
              <div v-for="file in episode.files" :key="file.item" class="torrent-file">
                <span class="torrent-kind" :class="`is-${file.kind}`">{{ kindWord(file.kind) }}</span>
                <span class="torrent-name" :title="file.path">{{ nameOf(file.path) }}</span>
                <NumberMark v-if="file.rule_number && file.by !== 'manual'" :n="file.rule_number" :title="howOf(file)" />
                <span v-else class="torrent-how" :class="{ 'is-hand': file.by === 'manual' }">{{ howOf(file) }}</span>
                <button v-if="file.by === 'manual'" type="button" class="torrent-mini" :disabled="busy" @click="back([file.item])">
                  {{ t("torrent.unassign") }}</button>
              </div>
            </template>
          </template>
          <button v-if="pick.count.value" type="button" class="torrent-drop drop-season" :disabled="busy"
                  v-bind="pick.target(toSeason(season.season, season.next))" @click="by(toSeason(season.season, season.next))()">
            <i class="pi pi-arrow-down" aria-hidden="true" />
            <span>{{ t("torrent.draft.dropSeason", { season: season.season, files: tn("torrent.files", pick.count.value), from: season.next }) }}</span>
          </button>
        </div>
      </div>
    </template>
  </TorrentPanes>
</template>
