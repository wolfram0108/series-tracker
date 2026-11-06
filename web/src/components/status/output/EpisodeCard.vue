<script setup lang="ts">
import { computed } from "vue"
import { t } from "../../../i18n"
import { outputLines, type EpisodeStatus, type EpisodeView, type SourceMark as Mark, type TrackCell } from "../../../lib/output"
import SourceMark from "./SourceMark.vue"
import TrackStrip from "./TrackStrip.vue"

// A card of an episode: coloured by its state; its number with the name of its file (when the card is wider than the
// smallest one); the source and the resolution of its reference; its tracks. One button: it opens the episode below its row.
const props = defineProps<{
  view: EpisodeView
  cells: TrackCell[]
  status: EpisodeStatus
  marks: Record<number, Mark>
  /** The name the file of the episode gets; shown in the first line when there is room. */
  file?: string
  open?: boolean
}>()
defineEmits<{ (e: "open"): void }>()

const references = computed(() => outputLines(props.view).filter((line) => line.kind === "reference"))
const reference = computed(() => references.value[0] ?? null)
const own = computed(() => props.view.references.some((each) => each.mark === "own" || each.mark === "other_player"))
const where = computed(() => {
  const line = reference.value
  if (!line) return t("output.reference.none")
  const height = line.height === null ? "" : ` ${t("output.height", { height: line.height })}`
  return `${line.playerLabel ?? line.player ?? ""}${height}`
})
const sourceId = computed(() => props.view.references.find((each) => each.source_id != null)?.source_id ?? null)
const label = computed(() => t("output.episode.card", { n: props.view.episode, status: t(`output.status.${props.status}`) }))
</script>

<template>
  <button
    type="button" class="episode-card" :class="[`is-${status}`, { 'is-open': open }]" :aria-expanded="open" :aria-label="label"
    :data-season="view.season" :data-episode="view.episode" @click="$emit('open')"
  >
    <span class="card-in">
      <span class="card-top">
        <span class="glass card-num">{{ view.episode }}</span>
        <span v-if="file" class="glass card-name" :title="file">{{ file }}</span>
        <span v-if="status === 'error' || own || status === 'off'" class="glass card-flag" aria-hidden="true">
          <i class="pi" :class="status === 'off' ? 'pi-power-off' : status === 'error' ? 'pi-exclamation-triangle' : 'pi-sliders-h'" />
        </span>
      </span>
      <span class="glass card-ref">
        <SourceMark :mark="sourceId === null ? null : marks[sourceId]" />
        <span>{{ where }}</span>
        <span v-if="references.length > 1" class="card-parts">+{{ references.length - 1 }}</span>
      </span>
      <TrackStrip class="glass card-strip" :cells="cells" />
    </span>
  </button>
</template>

<style scoped>
.episode-card {
  all: unset; box-sizing: border-box; position: relative; overflow: hidden; min-inline-size: 0; cursor: pointer;
  border-radius: calc(var(--card-row) * var(--round) + var(--card-pad)); box-shadow: var(--card-shadow); font: inherit;
}
.episode-card {
  --stripe: rgba(255, 255, 255, var(--stripe-intensity));
}
.episode-card::before {
  content: ""; position: absolute; inset: 0;
  background-image: linear-gradient(45deg, var(--stripe) 25%, transparent 25%, transparent 50%,
    var(--stripe) 50%, var(--stripe) 75%, transparent 75%, transparent 100%);
  background-size: 2.5rem 2.5rem;
}
.episode-card:hover { box-shadow: var(--card-hover-shadow); }
.episode-card:focus-visible, .episode-card.is-open { outline: 0.1875rem solid var(--color-blue); outline-offset: 0.125rem; }
.is-ready { background: linear-gradient(135deg, #4CAF50, #409444); }
/* Put together in part: the green of done with the gray stripes of what no source has. */
.is-partial { background: linear-gradient(135deg, #4CAF50, #409444); --stripe: rgba(108, 117, 125, 0.6); }
.is-down { background: linear-gradient(135deg, #009688, #00796D); }
.is-align { background: linear-gradient(135deg, var(--color-blue-1), color-mix(in srgb, var(--color-blue-1) 85%, black)); }
.is-wait { background: linear-gradient(135deg, #f0f2f5, #e9ecef); }
.is-error { background: linear-gradient(135deg, #dc3545, #c82333); }
.is-off { background: linear-gradient(135deg, #6c757d, #5a6268); }
.card-in { position: relative; display: flex; flex-direction: column; gap: 0.3rem; padding: var(--card-pad); }
/* Every line of a card is a box of one height, rounded by the same rule as the card around it. */
.glass {
  display: flex; align-items: center; min-block-size: var(--card-row); background: rgba(248, 249, 250, 0.82);
  border-radius: calc(var(--card-row) * var(--round)); box-shadow: 0 0 6px rgba(0, 0, 0, 0.15); color: #2c3e50;
}
.card-top { display: flex; align-items: center; gap: 0.375rem; min-inline-size: 0; }
.card-num { flex: none; padding-inline: 0.5rem; font-size: 1rem; font-weight: 700; line-height: 1; font-variant-numeric: tabular-nums; }
.card-name {
  flex: 1 1 auto; min-inline-size: 0; padding-inline: 0.5rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  font-family: var(--font-mono); font-size: 0.7rem;
}
.card-flag { flex: none; margin-inline-start: auto; padding-inline: 0.4rem; color: var(--color-red-1); }
.card-ref {
  align-self: flex-start; gap: 0.375rem; padding-inline: calc((var(--card-row) - var(--figure-size)) / 2) 0.5rem;
  font-size: 0.8rem; font-weight: 600; white-space: nowrap;
}
.card-parts { color: var(--text-muted); font-weight: 500; }
.card-strip { align-self: flex-start; }
</style>
