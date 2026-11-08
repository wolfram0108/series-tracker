<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue"
import { t } from "../../../i18n"
import type { Badge } from "../../../lib/compilations"
import {
  cutPictureOf, refVariants, widestFitting, type EpisodeStatus, type EpisodeView, type SourceMark as Mark, type TrackCell,
} from "../../../lib/output"
import PowerSwitch from "./PowerSwitch.vue"
import RefLine from "./RefLine.vue"
import SourceMark from "./SourceMark.vue"
import TrackStrip from "./TrackStrip.vue"

// A card of an episode: coloured by its state; its number with the name of its file (when the card is wider than the
// smallest one); the source and the resolution of its reference; its tracks. The card opens the episode below its row;
// the switch in its corner turns the episode on and off without opening it.
const props = defineProps<{
  view: EpisodeView
  cells: TrackCell[]
  status: EpisodeStatus
  marks: Record<number, Mark>
  /** The name the file of the episode gets; shown in the first line when there is room. */
  file?: string
  open?: boolean
  busy?: boolean
  /** The compilation the episode stands in (stage 12): its letter in the corner, in its colour. */
  badge?: Badge
  /** Said in place of the tracks (the view «Компиляции»: where an episode not taken stands). */
  note?: string
  /** What the switch says, when it is not the switch of the episode itself. */
  switchLabel?: string
}>()
defineEmits<{ (e: "open"): void; (e: "switch", off: boolean): void }>()

const references = computed(() => props.view.references.filter((each) => each.source_id != null))
const own = computed(() => props.view.references.some((each) => each.mark === "own" || each.mark === "other_player"))
// A cut episode names its compilation in the line of its source: the letter is not repeated in the corner.
const cut = computed(() => cutPictureOf(props.view))

// Priority+: the line of the source is the most detailed variant the card has room for. The widths come from a hidden copy of
// every variant, the room from the inside of the card; both are measured again when the card or the variants change.
const variants = computed(() => refVariants(props.view, props.marks))
const inner = ref<HTMLElement | null>(null)
const measure = ref<HTMLElement | null>(null)
const chosen = ref(0)
function fit(): void {
  if (!inner.value || !measure.value) return
  const style = getComputedStyle(inner.value)
  const room = inner.value.clientWidth - (parseFloat(style.paddingInlineStart) || 0) - (parseFloat(style.paddingInlineEnd) || 0)
  chosen.value = widestFitting([...measure.value.children].map((each) => (each as HTMLElement).offsetWidth), room)
}
let observer: ResizeObserver | null = null
onMounted(() => {
  fit()
  if (typeof ResizeObserver !== "undefined" && inner.value) {
    observer = new ResizeObserver(() => fit())
    observer.observe(inner.value)
  }
})
onBeforeUnmount(() => observer?.disconnect())
watch(variants, () => void nextTick(fit))
const label = computed(() => t("output.episode.card", { n: props.view.episode, status: t(`output.status.${props.status}`) }))
</script>

<template>
  <div
    class="episode-card" :class="[`is-${status}`, { 'is-open': open }]" :data-season="view.season" :data-episode="view.episode"
    @click="$emit('open')"
  >
    <!-- The whole card is the button: a press of it (or of Enter on it) comes up to the card as one click. -->
    <button type="button" class="card-hit" :aria-expanded="open" :aria-label="label" />
    <span ref="inner" class="card-in">
      <span class="card-top">
        <span class="glass card-num">{{ view.episode }}</span>
        <span v-if="file" class="glass card-name" :title="file">{{ file }}</span>
        <span class="card-corner">
          <span v-if="badge && !cut" class="glass card-flag card-badge" :style="{ '--tone': `var(--compilation-${badge.color})` }"
                :title="t('output.compilation.badge', { letter: badge.letter })">{{ badge.letter }}</span>
          <span v-if="status === 'error' || own" class="glass card-flag" aria-hidden="true">
            <i class="pi" :class="status === 'error' ? 'pi-exclamation-triangle' : 'pi-sliders-h'" />
          </span>
          <span class="glass card-power">
            <PowerSwitch
              :on="status !== 'off'" :busy="busy"
              :label="switchLabel ?? t(status === 'off' ? 'output.episode.enable' : 'output.episode.disable')"
              @toggle="$emit('switch', !$event)"
            />
          </span>
        </span>
      </span>
      <span v-if="variants.length" class="glass card-ref card-ref-shown">
        <RefLine :parts="variants[Math.min(chosen, variants.length - 1)]" :marks="marks" :badge="badge" />
        <span v-if="references.length > 1" class="card-parts">+{{ references.length - 1 }}</span>
      </span>
      <span v-else class="glass card-ref card-ref-shown"><SourceMark :mark="null" /><span>{{ t("output.reference.none") }}</span></span>
      <span ref="measure" class="card-ref-measure" aria-hidden="true">
        <span v-for="(parts, index) in variants" :key="index" class="glass card-ref">
          <RefLine :parts="parts" :marks="marks" :badge="badge" />
          <span v-if="references.length > 1" class="card-parts">+{{ references.length - 1 }}</span>
        </span>
      </span>
      <span v-if="note" class="glass card-note">{{ note }}</span>
      <TrackStrip v-else class="glass card-strip" :cells="cells" />
    </span>
  </div>
</template>

<style scoped>
.episode-card {
  box-sizing: border-box; position: relative; overflow: hidden; min-inline-size: 0; cursor: pointer;
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
.episode-card:has(.card-hit:focus-visible), .episode-card.is-open { outline: 0.1875rem solid var(--color-blue); outline-offset: 0.125rem; }
.is-ready { background: linear-gradient(135deg, #4CAF50, #409444); }
/* Put together in part: the ground and the stripes of the tokens (styles/tokens.css, --partial-*). */
.is-partial {
  background: linear-gradient(135deg, var(--partial-ground), var(--partial-ground-end));
  --stripe: color-mix(in srgb, var(--partial-stripe) var(--partial-stripe-share), transparent);
}
.is-down { background: linear-gradient(135deg, #009688, #00796D); }
.is-align { background: linear-gradient(135deg, var(--color-blue-1), color-mix(in srgb, var(--color-blue-1) 85%, black)); }
.is-wait { background: linear-gradient(135deg, #f0f2f5, #e9ecef); }
.is-error { background: linear-gradient(135deg, #dc3545, #c82333); }
.is-off { background: linear-gradient(135deg, #6c757d, #5a6268); }
.card-hit { all: unset; position: absolute; inset: 0; cursor: pointer; }
/* The lines let a press through to the button under them; only the switch takes it itself. */
.card-in { position: relative; pointer-events: none; display: flex; flex-direction: column; gap: 0.3rem; padding: var(--card-pad); }
/* Every line of a card is a box of one height, rounded by the same rule as the card around it. */
.glass {
  display: flex; align-items: center; min-block-size: var(--card-row); background: rgba(248, 249, 250, 0.82);
  border-radius: calc(var(--card-row) * var(--round)); box-shadow: 0 0 6px rgba(0, 0, 0, 0.15); color: #2c3e50;
}
.card-top { display: flex; align-items: center; gap: 0.375rem; min-inline-size: 0; }
.card-num { flex: none; padding-inline: 0.5rem; font-size: 1rem; font-weight: 700; line-height: 1; font-variant-numeric: tabular-nums; }
/* As wide as its text; what does not fit ends in an ellipsis (a block, as a flex box does not cut its text). */
.card-name {
  display: block; flex: 0 1 auto; min-inline-size: 0; padding-inline: 0.5rem; line-height: var(--card-row);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-family: var(--font-mono); font-size: 0.7rem;
}
.card-corner { flex: none; display: flex; align-items: center; gap: 0.375rem; margin-inline-start: auto; }
.card-flag { padding-inline: 0.4rem; color: var(--color-red-1); }
.card-badge { justify-content: center; min-inline-size: var(--card-row); background: var(--tone); color: #fff; font-weight: 700; }
.card-note {
  align-self: flex-start; padding-inline: 0.5rem; line-height: var(--card-row); font-size: 0.8rem; font-weight: 600;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-inline-size: 100%;
}
/* The switch is as high as the marks of the card; its box is a capsule around it, an even gap on every side. */
.card-power {
  --switch-size: var(--figure-size); justify-content: center; padding-inline: calc((var(--card-row) - var(--figure-size)) / 2);
  border-radius: calc(var(--card-row) / 2); pointer-events: auto;
}
.card-ref {
  align-self: flex-start; gap: 0.375rem; max-inline-size: 100%; min-inline-size: 0;
  padding-inline: calc((var(--card-row) - var(--figure-size)) / 2) 0.5rem; font-size: 0.8rem; font-weight: 600; white-space: nowrap;
}
.card-ref > .figure-mark { flex: none; }
.card-ref-shown { overflow: hidden; }
/* Every variant of the line, unseen, at its own width: what the card measures to choose the one it has room for. */
.card-ref-measure {
  position: absolute; inset-inline-start: 0; inset-block-start: 0; visibility: hidden; pointer-events: none;
  display: flex; flex-direction: column; align-items: flex-start;
}
.card-parts { flex: none; color: var(--text-muted); font-weight: 500; }
.card-strip { align-self: flex-start; }
</style>
