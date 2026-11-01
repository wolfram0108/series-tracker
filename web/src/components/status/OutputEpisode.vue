<script setup lang="ts">
import { computed, ref } from "vue"
import type { components } from "../../api/schema"
import { t, type MessageKey } from "../../i18n"
import { reasonText } from "../../lib/reason"
import { isDeviation, isOff, sourcePlayers, type EpisodeView, type Reference, type Scope } from "../../lib/output"
import OutputLevers from "./OutputLevers.vue"

type Schemas = components["schemas"]

// A compact card: what the episode takes and why; its levers open on demand so the list of episodes stays short.
const props = withDefaults(defineProps<{
  view: EpisodeView
  busy?: boolean
}>(), { busy: false })

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
const players = computed(() => sourcePlayers(props.view))
const deviation = computed(() => isDeviation(props.view))
const off = computed(() => isOff(props.view))

const episodeScope = (): Scope => ({ season: props.view.season, episode: props.view.episode })

// A player the episode no longer lists is named by its code, as the levers name it.
function playerOf(reference: Reference): string {
  const sourceId = reference.source_id ?? null
  const player = reference.player ?? ""
  return players.value.find((option) => option.sourceId === sourceId && option.player === player)?.playerLabel ?? player
}

function partText(reference: Reference): string | null {
  const part = reference.part ?? null
  return part === null ? null : t("output.part", { n: part })
}

// A reference with no source names no player, so it has no description; its mark still stands.
function sourceText(reference: Reference): string | null {
  if ((reference.source_id ?? null) === null) return null
  const group = reference.voice_group || t("output.lever.noGroup")
  const player = playerOf(reference)
  const height = reference.height ?? null
  return height === null
    ? t("output.lever.referenceLabel", { group, player })
    : t("output.lever.referenceLabelHeight", { group, player, height })
}

// The header names each reference by its part and its description; a reference with neither adds nothing.
const headings = computed(() => props.view.references
  .map((reference, index) => ({ key: index, part: partText(reference), source: sourceText(reference) }))
  .filter((heading) => heading.part !== null || heading.source !== null))

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
      <span v-for="heading in headings" :key="heading.key" class="cluster episode-reference">
        <span v-if="heading.part !== null">{{ heading.part }}</span>
        <span v-if="heading.source !== null">{{ heading.source }}</span>
      </span>
    </header>

    <ul v-if="view.references.length > 0 || view.rows.length > 0" class="episode-marks stack">
      <li v-for="(reference, index) in view.references" :key="index" class="cluster">
        <span v-if="partText(reference) !== null">{{ partText(reference) }}</span>
        <span>{{ t(MARK[reference.mark]) }}</span>
        <span v-if="reference.reason" class="episode-reason">{{ reasonText(reference.reason) }}</span>
      </li>
      <li v-if="view.references.length === 0 && view.rows.length > 0" class="cluster">
        <span>{{ t("output.mark.none") }}</span>
      </li>
    </ul>

    <section v-if="view.dubs.length > 0" class="episode-voices stack">
      <h4 class="episode-subtitle">{{ t("output.voices") }}</h4>
      <ul class="episode-dubs stack">
        <li v-for="dub in view.dubs" :key="dub.voice_key" class="cluster" :class="`dub-${dub.state}`">
          <span>{{ dub.voice_label }}</span>
          <span class="episode-state">{{ t(STATE[dub.state]) }}</span>
          <span v-if="dub.hint" class="episode-hint">{{ t(HINT[dub.hint]) }}</span>
          <span v-if="dub.detail" class="episode-reason">{{ reasonText(dub.detail) }}</span>
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
      v-if="open" scope="episode" :view="view" :levers="view.levers" :busy="busy"
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
.episode-marks, .episode-dubs { list-style: none; margin: 0; padding: 0; }
.episode-reference, .episode-reason { overflow-wrap: anywhere; }
.episode-state, .episode-hint, .episode-reason { color: var(--text-muted); font-size: smaller; }
.episode-toggle {
  padding: 0; border: none; background: none; cursor: pointer;
  color: var(--color-blue); text-decoration: underline;
}
.episode-toggle:disabled { cursor: default; opacity: 0.5; }
</style>
