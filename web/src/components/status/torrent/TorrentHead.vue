<script setup lang="ts">
import { computed, ref, watch } from "vue"
import Select from "primevue/select"
import ToggleSwitch from "primevue/toggleswitch"
import Button from "primevue/button"
import { t, type MessageKey } from "../../../i18n"
import type { SeriesSource } from "../../../lib/sources"
import { siteOf } from "../../../lib/sources"
import { RELEASE_STATES, stateIcon, stateLabel } from "../../../lib/states"
import { EDITOR_TABS, type Editor, type EditorTab, type PageReleases, type ReleaseView, type SourceState } from "../../../lib/torrent"
import { downloadRelease, watchRelease } from "../../../composables/torrentCalls"
import SourceMark from "../output/SourceMark.vue"

// The head of the tab «Торрент» (the board «Состояния»): the source, its release and its state, the progress of what
// goes on, the automatic update, the download of a release not loaded yet, and the switch of the four editors.
const props = defineProps<{
  source: SeriesSource
  place: number
  page: PageReleases | null
  release: ReleaseView | undefined
  state: SourceState
  editor: Editor | null
  tab: EditorTab
  counts: Record<EditorTab, number>
}>()
const emit = defineEmits<{ tab: [EditorTab]; failure: [string] }>()

const named = computed(() => RELEASE_STATES[props.state.stage])
const mark = computed(() => ({ n: props.place, color: props.source.color, place: props.source.url }))
const title = computed(() => props.release?.title ?? chosenRelease.value?.title ?? props.page?.title ?? "")

// What the head counts of the tab shown: the files of the release, its sounds, its subtitles and fonts, its containers.
const told = computed(() => {
  const view = props.editor?.view
  if (!view) return ""
  if (props.tab === "files") return t("torrent.head.files", { n: view.tabs.files.length })
  if (props.tab === "voices") return t("torrent.head.voices", { n: view.tabs.voices.length })
  if (props.tab === "subtitles") return t("torrent.head.subtitles", { n: view.tabs.subtitles.length,
    fonts: view.tabs.files.filter((file) => file.kind === "font").length })
  return t("torrent.head.streams", { n: view.containers.length })
})

const progress = computed(() => {
  const shown = props.state.progress
  if (!shown || !shown.total) return null
  const key: MessageKey = props.state.stage === "read" ? "torrent.progress.read" : "torrent.progress.files"
  return { share: shown.share, text: t(key, shown) }
})

// A release not loaded yet is chosen among those the page lists; one of them is chosen by itself.
const chosen = ref<string | null>(null)
watch(() => props.page, (page) => {
  if (page && !page.releases.some((each) => each.release === chosen.value)) chosen.value = page.releases[0]?.release ?? null
}, { immediate: true })
const chosenRelease = computed(() => props.page?.releases.find((each) => each.release === chosen.value) ?? null)
const options = computed(() => (props.page?.releases ?? []).map((each) => ({ value: each.release,
  label: each.episodes ? `${each.title} · ${each.episodes}` : each.title })))

const busy = ref(false)
async function download(): Promise<void> {
  if (!chosen.value) return
  busy.value = true
  try {
    const answer = await downloadRelease(props.source.url, chosen.value)
    if (!answer.ok) emit("failure", answer.text)
  } finally {
    busy.value = false
  }
}

// A release not loaded yet is watched by default (torrent_releases.watched); the switch says so until it is.
async function setWatched(watched: boolean): Promise<void> {
  if (!props.release) return
  busy.value = true
  try {
    const answer = await watchRelease(props.source.url, props.release.release, watched)
    if (!answer.ok) emit("failure", answer.text)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <!-- The line of the source (the boards «Файлы» and «Состояния»): what it is and how it stands, then its controls and the
       switch of the editors at its end. -->
  <div class="torrent-head">
    <SourceMark :mark="mark" />
    <b class="torrent-site">{{ page?.site || siteOf(source.url) }}</b>
    <span class="torrent-title" :title="title">{{ title }}<template v-if="told"> · {{ told }}</template></span>
    <span class="pill torrent-state" :data-state="named"><i class="pi" :class="stateIcon(named)" aria-hidden="true" />{{ stateLabel(named) }}</span>
    <span v-if="progress" class="torrent-progress" :data-state="named">
      <span class="torrent-bar" role="progressbar" :aria-valuenow="progress.share" aria-valuemin="0" aria-valuemax="100">
        <span :style="{ inlineSize: `${progress.share}%` }" />
      </span>{{ progress.text }}
    </span>
    <span v-if="state.stage === 'ready' && state.waiting" class="torrent-waiting">
      {{ t("torrent.progress.waiting", { n: state.waiting }) }}
    </span>
    <span v-if="state.stage === 'update'" class="torrent-muted">{{ t("torrent.unavailable") }}</span>
    <span v-if="state.stage === 'failed' && state.reason" class="torrent-muted">{{ state.reason }}</span>

    <div class="torrent-actions">
      <label class="torrent-watch">
        {{ t("torrent.watch") }}
        <ToggleSwitch :model-value="release?.watched ?? true" :disabled="!release || busy" :aria-label="t('torrent.watch')"
                      @update:model-value="setWatched" />
      </label>
      <template v-if="state.stage === 'notLoaded'">
        <span v-if="!page" class="torrent-muted">{{ t("torrent.release.reading") }}</span>
        <span v-else-if="!page.releases.length" class="torrent-muted">{{ t("torrent.release.none") }}</span>
        <Select v-else-if="page.releases.length > 1" v-model="chosen" :options="options" option-label="label" option-value="value"
                :aria-label="t('torrent.release')" class="torrent-release" />
        <Button :label="t('torrent.download')" icon="pi pi-download" :disabled="!chosen || busy" :loading="busy"
                class="do-download" @click="download" />
      </template>
      <div v-if="editor && state.stage !== 'notLoaded'" class="st-tabs st-tabs-wrap torrent-switch" role="tablist">
        <button v-for="each in EDITOR_TABS" :key="each" type="button" role="tab" class="st-tab" :class="[`tab-${each}`, { active: tab === each }]"
                :aria-selected="tab === each" @click="emit('tab', each)">
          {{ t(`torrent.tab.${each}` as MessageKey) }}<span v-if="counts[each]" class="torrent-count">{{ counts[each] }}</span>
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.torrent-head { display: flex; flex-wrap: wrap; align-items: center; gap: 0.5rem 0.65rem; }
.torrent-site { font-size: 0.95rem; }
.torrent-title { flex: 1 1 8rem; min-inline-size: 6rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--text-muted); }
.torrent-state { font-size: 0.75rem; font-weight: 600; }
.torrent-state i { font-size: 0.75rem; }
.torrent-progress { display: inline-flex; align-items: center; gap: 0.6rem; font-size: 0.8rem; color: var(--text-muted); white-space: nowrap; }
.torrent-bar { display: block; inline-size: 8.75rem; block-size: 0.5rem; border-radius: 999px; background: var(--border-color); overflow: hidden; }
.torrent-bar span { display: block; block-size: 100%; border-radius: 999px; background: var(--tone-end); }
.torrent-waiting {
  display: inline-flex; align-items: center; padding: 0.1rem 0.6rem; border-radius: 999px; font-size: 0.78rem; font-weight: 600;
  background: color-mix(in srgb, var(--state-absent) 30%, var(--bg-white)); color: var(--state-absent-ink);
  border: 1px solid var(--state-absent-end); white-space: nowrap;
}
.torrent-muted { color: var(--text-muted); font-size: 0.85rem; }
.torrent-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 0.75rem; margin-inline-start: auto; }
.torrent-watch { display: inline-flex; align-items: center; gap: 0.6rem; color: var(--text-muted); font-size: 0.875rem; }
.torrent-release { min-inline-size: 14rem; max-inline-size: 28rem; }
.torrent-count {
  display: inline-flex; align-items: center; justify-content: center; min-inline-size: 1.25rem; block-size: 1.125rem; padding: 0 0.375rem;
  margin-inline-start: 0.4rem; border-radius: 999px; background: var(--state-failed); color: var(--bg-white); font-size: 0.7rem; font-weight: 700;
}
</style>
