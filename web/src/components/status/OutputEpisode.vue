<script setup lang="ts">
import { computed, ref } from "vue"
import type { components } from "../../api/schema"
import { hasMessage, t, type MessageKey } from "../../i18n"
import { reasonText } from "../../lib/reason"
import { isDeviation, isOff, outputLines, type EpisodeView, type LeverSpec, type OutputLine, type Scope } from "../../lib/output"
import OutputLevers from "./OutputLevers.vue"

type Schemas = components["schemas"]

// A compact card: what the episode takes and why; its levers open on demand so the list of episodes stays short.
const props = withDefaults(defineProps<{
  view: EpisodeView
  /** The levers there are, as the core describes them. */
  specs: LeverSpec[]
  busy?: boolean
  /** The names the files of the episode would get; shown when the window asked for them. */
  fileNames?: string[]
}>(), { busy: false, fileNames: () => [] })

const emit = defineEmits<{
  (e: "set", scope: Scope, lever: string, target: string | null, value: unknown): void
  (e: "reset", scope: Scope, lever: string, target: string | null): void
  (e: "switch", season: number, episode: number, off: boolean): void
}>()

// The keys are written out, never built from a value, so every text stays findable in the catalog.
const MARK: Record<Schemas["ReferenceMark"], MessageKey> = {
  by_level: "output.mark.by_level",
  own: "output.mark.own",
  other_player: "output.mark.other_player",
  none: "output.mark.none",
  unfit: "output.mark.unfit",
  off: "output.mark.off",
}

const STATE: Record<Schemas["DubState"], MessageKey> = {
  planned: "voices.state.planned",
  download_queued: "voices.state.download_queued",
  downloading: "voices.state.downloading",
  align_queued: "voices.state.align_queued",
  running: "voices.state.running",
  done: "voices.state.done",
  error: "voices.state.error",
}

const HINT: Record<Schemas["DubHint"], MessageKey> = {
  planned: "voices.hint.planned",
  download_queued: "voices.hint.download_queued",
  downloading: "voices.hint.downloading",
  ready: "voices.hint.ready",
  awaiting_answer: "voices.hint.awaiting_answer",
  aligning_wait: "voices.hint.aligning_wait",
  aligning_run: "voices.hint.aligning_run",
  aligned: "voices.hint.aligned",
  done: "voices.hint.done",
}

const open = ref(false)
const lines = computed(() => outputLines(props.view))
const deviation = computed(() => isDeviation(props.view))
const off = computed(() => isOff(props.view))

const episodeScope = (): Scope => ({ season: props.view.season, episode: props.view.episode })

// The status of the video of a reference is the status of its file; a status the catalog has no words for is shown as the core gave it.
function fileStatusText(status: string | null): string {
  if (status === null) return t("output.file.status.none")
  const key = `output.file.status.${status}`
  return hasMessage(key) ? t(key) : status
}

// One line: the reference (marked) or the voice-over, the variant it comes from and its status.
function lineText(line: OutputLine): string {
  const group = line.group || t("output.lever.noGroup")
  const player = line.playerLabel ?? line.player
  const where = player === null
    ? t("output.line.unknownSource")
    : line.height === null ? t("output.line.where", { player }) : t("output.line.whereHeight", { player, height: line.height })
  const status = line.dub === null ? fileStatusText(line.file) : t(STATE[line.dub.state])
  return t(line.kind === "reference" ? "output.line.reference" : "output.line.dub", { group, where, status })
}

function forwardSet(lever: string, target: string | null, value: unknown): void {
  emit("set", episodeScope(), lever, target, value)
}

function forwardReset(lever: string, target: string | null): void {
  emit("reset", episodeScope(), lever, target)
}
</script>

<template>
  <article
    class="output-episode stack" :data-season="view.season" :data-episode="view.episode"
    :class="{ 'is-deviation': deviation, 'is-off': off }"
  >
    <header class="cluster">
      <h3 class="episode-title">{{ t("output.episode", { n: view.episode }) }}</h3>
    </header>

    <ul v-if="view.references.length > 0 || view.rows.length > 0" class="episode-marks stack">
      <li v-for="(reference, index) in view.references" :key="index" class="cluster">
        <span v-if="reference.part != null">{{ t("output.part", { n: reference.part }) }}</span>
        <span>{{ t(MARK[reference.mark]) }}</span>
        <span v-if="reference.reason" class="episode-reason">{{ reasonText(reference.reason) }}</span>
      </li>
      <li v-if="view.references.length === 0 && view.rows.length > 0" class="cluster">
        <span>{{ t("output.mark.none") }}</span>
      </li>
    </ul>

    <ul v-if="fileNames.length > 0" class="episode-files stack">
      <li v-for="name in fileNames" :key="name" class="episode-file">{{ t("output.file", { name }) }}</li>
    </ul>

    <section v-if="lines.length > 0" class="episode-voices stack">
      <h4 class="episode-subtitle">{{ t("output.voices") }}</h4>
      <ul class="episode-dubs stack">
        <li v-for="line in lines" :key="line.key" class="cluster" :class="line.dub ? `dub-${line.dub.state}` : 'is-reference'">
          <span class="episode-line">{{ lineText(line) }}</span>
          <span v-if="line.part !== null" class="episode-part">{{ t("output.part", { n: line.part }) }}</span>
          <span v-if="line.dub?.hint" class="episode-hint">{{ t(HINT[line.dub.hint]) }}</span>
          <span v-if="line.dub?.detail" class="episode-reason">{{ reasonText(line.dub.detail) }}</span>
        </li>
      </ul>
    </section>

    <div class="cluster">
      <button type="button" class="episode-toggle" :aria-expanded="open" @click="open = !open">
        {{ t(open ? "output.levers.hide" : "output.levers.show") }}
      </button>
      <button
        type="button" class="episode-toggle episode-switch" :disabled="busy"
        @click="emit('switch', view.season, view.episode, !off)"
      >{{ t(off ? "output.episode.enable" : "output.episode.disable") }}</button>
    </div>
    <OutputLevers
      v-if="open" scope="episode" :view="view" :levers="view.levers" :specs="specs" :busy="busy"
      @set="forwardSet" @reset="forwardReset"
    />
  </article>
</template>

<style scoped>
.output-episode {
  padding: var(--space); border: var(--border-width) solid var(--border-color);
  border-radius: var(--border-radius); background: var(--bg-white);
}
.episode-title, .episode-subtitle { margin: 0; font-size: 1em; }
.episode-marks, .episode-dubs, .episode-files { list-style: none; margin: 0; padding: 0; }
.episode-file { overflow-wrap: anywhere; color: var(--text-muted); font-size: smaller; }
.episode-line, .episode-reason { overflow-wrap: anywhere; }
.episode-state, .episode-hint, .episode-reason { color: var(--text-muted); font-size: smaller; }
.episode-toggle {
  padding: 0; border: none; background: none; cursor: pointer;
  color: var(--color-blue); text-decoration: underline;
}
.episode-toggle:disabled { cursor: default; opacity: 0.5; }
</style>
