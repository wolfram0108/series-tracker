<script setup lang="ts">
import type { RulePlace } from "../../../lib/ruleVocabulary"
import { computed, onMounted, onUnmounted, ref, watch } from "vue"
import StTab from "../../StTab.vue"
import { t, type MessageKey } from "../../../i18n"
import type { SeriesSource } from "../../../lib/sources"
import { EDITOR_TABS, sourceState, type Editor, type EditorTab, type PageReleases, type ReleaseView } from "../../../lib/torrent"
import { useSSE } from "../../../composables/useSSE"
import { useBusyBar } from "../../../composables/useWindowBusy"
import { fetchEditor, fetchPage, fetchReleases } from "../../../composables/torrentCalls"
import RulesEditor from "../../settings/RulesEditor.vue"
import RulesAddButtons from "../../settings/RulesAddButtons.vue"
import TorrentHead from "./TorrentHead.vue"
import TorrentFiles from "./TorrentFiles.vue"
import TorrentTracks from "./TorrentTracks.vue"
import TorrentStreams from "./TorrentStreams.vue"

// The tab «Торрент» of the window of a series (stage 15, step 9; the mockup the owner accepted): the release of a torrent
// source — its state, its download and update — and the editor of its files: «Файлы» (the preset of the torrents puts them
// in seasons and episodes), «Озвучки» and «Субтитры» (folders of the tracks), «Контейнеры» (the tracks inside the files).
// Every change is one request; what it makes comes back as events of the core, never by asking again on a timer.
const props = defineProps<{ sources: SeriesSource[] }>()

const torrents = computed(() => props.sources.filter((source) => source.source_type === "torrent"))
const chosenId = ref<number | null>(null)
const source = computed(() => torrents.value.find((each) => each.id === chosenId.value) ?? torrents.value[0] ?? null)
const place = computed(() => source.value ? props.sources.findIndex((each) => each.id === source.value!.id) : -1)

const page = ref<PageReleases | null>(null)
const kept = ref<ReleaseView[]>([])
const editor = ref<Editor | null>(null)
const reading = ref<Record<string, { done: number; total: number }>>({})
const failure = ref("")
const loading = ref(false)
useBusyBar(() => loading.value)

const tab = ref<EditorTab>("files")
// The rules start folded: the panes of the release are what the tab is opened for; «Добавить» or the head opens them.
const rulesOpen = ref(false)

// One release is edited: the one the core keeps for the page (a page of several releases keeps the one the user downloaded).
const release = computed(() => kept.value[0])
const state = computed(() => sourceState(release.value, release.value ? reading.value[release.value.release]
  ?? editor.value?.view.releases.find((each) => each.release === release.value!.release)?.reading ?? null : null,
  EDITOR_TABS.reduce((sum, each) => sum + (editor.value?.view.unplaced[each] ?? 0), 0)))

let asked = 0
async function load(): Promise<void> {
  const url = source.value?.url
  if (!url) return
  const ask = ++asked
  loading.value = true
  failure.value = ""
  try {
    const [releases, opened] = await Promise.all([fetchReleases(url), fetchEditor(url)])
    if (ask !== asked) return
    if (releases.ok) kept.value = releases.data.releases
    else failure.value = releases.text
    if (opened.ok) editor.value = opened.data
    else failure.value = opened.text
    // The page is read only while nothing is downloaded: it lists the releases the user picks from.
    if (!kept.value.length && !page.value) {
      const listed = await fetchPage(url)
      if (ask !== asked) return
      if (listed.ok) page.value = listed.data
      else failure.value = listed.text
    }
  } finally {
    if (ask === asked) loading.value = false
  }
}

async function reloadEditor(): Promise<void> {
  const url = source.value?.url
  if (!url) return
  const opened = await fetchEditor(url)
  if (opened.ok && url === source.value?.url) editor.value = opened.data
}

watch(source, () => {
  page.value = null
  kept.value = []
  editor.value = null
  reading.value = {}
  void load()
})

// The news of the core: a release changed (state, files, progress), the editor of the page changed, a read went on. The
// address the events name is the canonical one of the page; the editor answers with the same.
const sse = useSSE()
const urlOf = (): string => editor.value?.url ?? source.value?.url ?? ""
const offs = [
  sse.on("torrent_release_update", (data) => {
    const view = data as ReleaseView
    if (view.url !== urlOf() && view.url !== source.value?.url) return
    kept.value = [view, ...kept.value.filter((each) => each.release !== view.release)]
  }),
  sse.on("torrent_editor_update", (data) => {
    if ((data as { url: string }).url === urlOf()) void reloadEditor()
  }),
  sse.on("torrent_read_update", (data) => {
    const told = data as { url: string; release: string; done: number; total: number }
    if (told.url === urlOf()) reading.value = { ...reading.value, [told.release]: { done: told.done, total: told.total } }
  }),
]
onMounted(() => void load())
onUnmounted(() => offs.forEach((off) => off()))

const counts = computed(() => editor.value?.view.unplaced ?? { files: 0, voices: 0, subtitles: 0, voice_streams: 0, subtitle_streams: 0 })
const lines = computed(() => {
  const view = editor.value?.view
  if (!view) return []
  return tab.value === "files" ? view.tabs.files.map((each) => each.line) : view.tabs[tab.value].map((each) => each.line)
})
// The codes of the fields of the lines of the tab, as the quick templates of a table offer them.
const codes = computed(() => [...new Set(lines.value.flatMap((line) => line.split(" | ")
  .map((part) => /^([A-Z]{1,3}):/.exec(part)?.[1] ?? "").filter(Boolean)))])
const profileId = computed(() => editor.value?.profiles[tab.value] ?? null)
// The place of the constructor of the tab: «ТО» offers only what applies there.
// A rule or a table added from the head opens the rules, so the new card is seen.
const rulesEditor = ref<InstanceType<typeof RulesEditor> | null>(null)
function addTo(what: "addRule" | "addTable"): void {
  rulesOpen.value = true
  rulesEditor.value?.[what]()
}
const rulePlace = computed<RulePlace>(() => tab.value === "files" ? "files"
  : tab.value === "voices" || tab.value === "voice_streams" ? "voices" : "subtitles")
</script>

<template>
  <div class="torrent-tab">
    <div v-if="torrents.length > 1" class="torrent-sources st-tabs st-tabs-wrap" role="tablist" :aria-label="t('torrent.sources')">
      <StTab v-for="each in torrents" :key="each.id" :active="each.id === source?.id" :label="each.url" @click="chosenId = each.id" />
    </div>
    <template v-if="source">
      <TorrentHead
        :source="source" :place="place + 1" :page="page" :release="release" :state="state" :editor="editor"
        :tab="tab" :counts="counts" @tab="tab = $event" @failure="failure = $event"
      />
      <p v-if="failure" class="sheet-problem torrent-failure">{{ failure }}</p>
      <p v-if="state.stage === 'notLoaded'" class="torrent-hint">{{ t("torrent.notLoaded.hint") }}</p>
      <template v-else-if="editor">
        <section class="torrent-rules">
          <!-- The whole bar folds the rules (its title button is the one of the keyboard); the chevron stands after the buttons. -->
          <div class="torrent-rules-bar" @click="rulesOpen = !rulesOpen">
            <button type="button" class="torrent-rules-head" :aria-expanded="rulesOpen">
              <span>{{ t(`torrent.rules.${tab}` as MessageKey) }}</span>
            </button>
            <RulesAddButtons v-if="profileId !== null" :disabled="rulesEditor?.isLoading ?? true"
                             @rule="addTo('addRule')" @table="addTo('addTable')" />
            <i class="pi torrent-rules-chevron" :class="rulesOpen ? 'pi-chevron-up' : 'pi-chevron-down'" aria-hidden="true" />
          </div>
          <div v-show="rulesOpen" class="torrent-rules-body">
            <RulesEditor v-if="profileId !== null" ref="rulesEditor" :key="profileId" :profile-id="profileId" :lines="lines"
                         :codes="codes" :place="rulePlace" />
          </div>
        </section>
        <TorrentFiles v-if="tab === 'files'" :editor="editor" :release="release" @failure="failure = $event" />
        <TorrentTracks v-else-if="tab === 'voices' || tab === 'subtitles'" :key="tab" :editor="editor" :tab="tab" @failure="failure = $event" />
        <TorrentStreams v-else :key="tab" :editor="editor" :tab="tab" @failure="failure = $event" />
      </template>
    </template>
  </div>
</template>

<style scoped>
/* The tab fills the body of the window: the rules above, the two panes under them take the rest and scroll inside. */
.torrent-tab { display: flex; flex-direction: column; gap: 0.875rem; block-size: 100%; }
.torrent-hint { margin: 0; color: var(--text-muted); }
.torrent-failure { margin: 0; }
.torrent-rules { flex: none; border: 1px solid var(--border-color); border-radius: var(--border-radius); overflow: hidden; }
/* The head: the title folds the rules, the buttons beside it add a rule or a table. */
.torrent-rules-bar {
  display: flex; flex-wrap: wrap; align-items: center; gap: 0.375rem 0.625rem; padding: 0.3125rem 0.75rem; background: #cfe2ff;
  color: #0b5cd4; cursor: pointer;
}
.torrent-rules-chevron { flex: none; }
.torrent-rules-head {
  display: flex; flex: 1 1 12rem; align-items: center; gap: 0.625rem; padding: 0.25rem 0;
  border: 0; background: none; color: #0b5cd4; font: inherit; font-size: 0.94rem; cursor: pointer; text-align: start;
}
.torrent-rules-body { padding: 0.5rem 0.75rem; }
</style>
