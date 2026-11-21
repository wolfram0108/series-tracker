<script setup lang="ts">
import { computed } from "vue"
import type { Queues } from "../../../composables/outputCalls"
import type { FailedAttempt, HeldFile, Taken } from "../../../lib/plan"
import type { SameGroups } from "../../../lib/same"
import { useCardGrid } from "../../../composables/useCardGrid"
import { useShownCells } from "../../../composables/useShownCells"
import { sharing } from "../../../lib/sharing"
import { t } from "../../../i18n"
import {
  badgeOf, cardStatus, cutOf, cutState, heldCut, shareOf, takes, type CompilationView, type CutItem, type CutState,
} from "../../../lib/compilations"
import { isOff, trackCells, type EpisodeView, type QueueName, type QueueRow, type SourceMark as Mark } from "../../../lib/output"
import type { CompilationRow } from "./CompilationTable.vue"
import EpisodeCard from "./EpisodeCard.vue"
import EpisodeDetail from "./EpisodeDetail.vue"
import FoldButton from "../../FoldButton.vue"
import PowerSwitch from "./PowerSwitch.vue"
import SourceMark from "./SourceMark.vue"
import { CUT_STATES, stateWord } from "../../../lib/states"

// A compilation of a season in the view «Компиляции» (stage 12): a block like that of a season, for the episodes the
// compilation covers. The switch of a card says whether the episode stands in this compilation (it stands in one at most:
// switching it on here takes it out of the other); the colour of a card is its cut; an episode that is not taken says where
// it stands. The header switches the whole compilation and opens its settings (REF 2 for all its episodes).
const props = withDefaults(defineProps<{
  view: CompilationView
  /** Every compilation of the season: the letter of the one an episode stands in, and the rows of an opened episode. */
  season: CompilationView[]
  episodes: EpisodeView[]
  cuts: Map<string, CutItem>
  marks: Record<number, Mark>
  /** The label of each player of each source ("source:player"). */
  labels: Record<string, string>
  /** The voice-overs of the season in the order of the tracks: the strip of a card. */
  order: string[]
  mode?: "cards" | "tiles"
  opened?: number | null
  queues?: Queues
  /** What the file of the opened episode holds now: the fact its queues are read against. */
  held?: HeldFile | null
  /** The variant the plan takes for the reference of the opened episode. */
  taken?: Taken | null
  /** The attempts of the plan that failed for the opened episode: its queues mark the rows they were of. */
  failed?: FailedAttempt[]
  busy?: boolean
}>(), { mode: "cards", opened: null, busy: false, taken: null, failed: () => [] })

const emit = defineEmits<{
  (e: "open", episode: number | null): void
  (e: "settings"): void
  (e: "switch", off: boolean): void
  (e: "member", episode: number, key: string): void
  (e: "retry", episode: number): void
  (e: "retry-failed", episode: number): void
  (e: "queue-order", episode: number, queue: QueueName, group: string | null, rows: QueueRow[]): void
  (e: "queue-allow", episode: number, queue: QueueName, row: QueueRow, allowed: boolean): void
  (e: "queue-reset", episode: number, queue: QueueName, group: string | null): void
  (e: "queue-same", episode: number, queue: QueueName, groups: SameGroups | null): void
}>()

const badge = computed(() => badgeOf(props.view))
const head = computed(() => props.view.ref)
const range = (view: CompilationView) => `${view.ref.start}–${view.ref.end}`
const playerOf = (view: CompilationView) => props.labels[`${view.ref.source_id}:${view.ref.player}`] ?? view.ref.player
const byEpisode = computed(() => new Map(props.episodes.map((view) => [view.episode, view] as const)))
// The height of the reference an episode has now (a video held on disk may be higher than the layout shows).
const height = (_season: number, episode: number) => {
  const view = byEpisode.value.get(episode)
  return view && isOff(view) ? "off" as const : view?.references.find((each) => each.source_id != null)?.height ?? null
}

// The card of an episode the news left as it was is given the same props and is not drawn again (lib/sharing.ts).
const items = sharing(() => (props.view.episodes ?? []).flatMap((each) => {
  const view = byEpisode.value.get(each.episode)
  if (!view) return []
  const member = takes(props.view, each.episode, height)
  const cut = member ? cutOf(props.cuts, props.view.season, each.episode) : undefined
  const status = cardStatus(member, cut, heldCut(view, props.view.ref.key))
  const elsewhere = each.elsewhere ? props.season.find((other) => other.ref.key === each.elsewhere) : undefined
  const absence = isOff(view) ? "episode_off" : each.member && !member ? "not_better" : each.absence ?? "own"
  const note = member ? undefined : elsewhere
    ? t("output.compilation.absence.elsewhere", { letter: badgeOf(elsewhere).letter, range: range(elsewhere) })
    : t(`output.compilation.absence.${absence}`)
  const fits = props.view.take && absence !== "not_better" && absence !== "episode_off" && (member || each.absence === "elsewhere" || each.absence === "own")
  return [{ view, each: { ...each, member }, cut, status, note, fits, cells: trackCells(view, props.order) }]
}))

const cutCounts = computed(() => {
  const counts: Record<CutState, number> = { wait: 0, down: 0, align: 0, ready: 0, error: 0 }
  for (const item of items.value) {
    if (item.each.member) counts[item.status as CutState] += 1
  }
  return counts
})
const takenCount = computed(() => items.value.filter((item) => item.each.member).length)
const share = (count: number, all: number): string => `${all === 0 ? 0 : (count * 100) / all}%`
const cutText = computed(() => (Object.entries(cutCounts.value) as [CutState, number][])
  .filter(([, count]) => count > 0).map(([state, count]) => t("state.count", { state: stateWord(CUT_STATES[state]), n: count })).join(" · "))

// The rows hold as many cards as those of the season: the block is a copy of the season's for a part of its episodes.
const { grid, ruler, columns } = useCardGrid(() => props.mode, () => props.order.length, () => props.episodes.length)
// A card is drawn while it is near the view (composables/useShownCells.ts), as in a season.
const { built, cell, sizeOf } = useShownCells(grid)

const opened = computed(() => items.value.find((item) => item.view.episode === props.opened) ?? null)
function step(by: number): void {
  const at = items.value.findIndex((item) => item.view.episode === props.opened)
  const next = items.value[at + by]
  if (next) emit("open", next.view.episode)
}
// The compilations that cover the opened episode, as its tab «Компиляции» shows them.
const rows = computed<CompilationRow[]>(() => {
  const episode = props.opened
  if (episode === null) return []
  return props.season.filter((view) => view.ref.start <= episode && episode <= view.ref.end).map((view) => {
    const each = (view.episodes ?? []).find((one) => one.episode === episode)
    const member = takes(view, episode, height)
    const cut = member ? cutOf(props.cuts, view.season, episode) : undefined
    const held = heldCut(byEpisode.value.get(episode), view.ref.key)
    return {
      view, badge: badgeOf(view), member, player: playerOf(view),
      cut: member ? cutState(cut) ?? (held ? "ready" : "wait") : null, share: shareOf(cut ?? held),
      fits: view.take && (member || ((each?.absence === "elsewhere" || each?.absence === "own")
        && height(view.season, episode) !== "off" && (Number(height(view.season, episode)) || 0) < (view.ref.height ?? 0))),
    }
  })
})
</script>

<template>
  <section class="season-block panel compilation-block fold-room" :data-compilation="head.key">
    <header class="season-head fold-head">
      <div class="fold-said">
      <span class="compilation-letter" :style="{ '--tone': `var(--compilation-${badge.color})` }">{{ badge.letter }}</span>
      <span class="season-title">{{ t("output.compilation.title", { letter: badge.letter, range: range(view) }) }}</span>
      <span class="season-range compilation-source">
        <SourceMark :mark="marks[head.source_id]" />{{ marks[head.source_id]?.place }}
        · {{ t("output.compilation.what", { player: playerOf(view), height: head.height ?? "—", count: (view.episodes ?? []).length }) }}
      </span>
      <div class="season-meters">
        <div class="meter">
          <span class="meter-name">{{ t("output.meter.episodes") }}</span>
          <span class="meter-bar"><span data-state="align" :style="{ inlineSize: share(takenCount, items.length) }" /></span>
          <span class="meter-text">{{ t("output.meter.taken", { n: takenCount, total: items.length }) }}</span>
        </div>
        <div v-if="takenCount" class="meter">
          <span class="meter-name">{{ t("output.meter.cut") }}</span>
          <span class="meter-bar">
            <span v-for="(count, state) in cutCounts" :key="state" :class="`is-${state === 'ready' ? 'done' : state}`"
                  :style="{ inlineSize: share(count, takenCount) }" />
          </span>
          <span class="meter-text">{{ cutText }}</span>
        </div>
      </div>
      </div>
      <div class="fold-does">
      <FoldButton :label="t('output.compilation.settings')" icon="pi pi-sliders-h" severity="info" fold="xl" class="season-settings"
                  @click="emit('settings')" />
      <PowerSwitch :on="view.take" :busy="busy" :label="t(view.take ? 'output.compilation.disable' : 'output.compilation.enable')"
                   @toggle="emit('switch', !$event)" />
      </div>
    </header>
    <div class="season-body">
      <div ref="grid" class="season-grid" :class="`is-${mode}`" :style="columns">
        <span ref="ruler" class="grid-ruler" aria-hidden="true" />
        <template v-for="item in items" :key="item.view.episode">
          <EpisodeCard
            v-if="mode === 'cards' && (built(item.view.episode) || opened?.view.episode === item.view.episode)" :ref="cell(item.view.episode)"
            class="cell-in" :view="item.view" :cells="item.cells" :status="item.status" :marks="marks"
            :open="opened?.view.episode === item.view.episode" :busy="busy || !item.fits"
            :badge="item.each.member ? badge : undefined" :note="item.note"
            :switch-label="t(item.each.member ? 'output.compilation.leave' : 'output.compilation.take')"
            @open="emit('open', opened?.view.episode === item.view.episode ? null : item.view.episode)"
            @switch="emit('member', item.view.episode, $event ? '' : head.key)"
          />
          <div
            v-else-if="mode === 'cards'" :ref="cell(item.view.episode)" class="episode-card card-shell" :style="sizeOf(item.view.episode)"
            :data-episode="item.view.episode" aria-hidden="true"
          />
          <button
            v-else type="button" class="episode-tile" :data-episode="item.view.episode"
            :class="[`is-${item.status}`, { 'is-open': opened?.view.episode === item.view.episode }]"
            :title="item.note" @click="emit('open', opened?.view.episode === item.view.episode ? null : item.view.episode)"
          >{{ item.view.episode }}</button>
          <EpisodeDetail
            v-if="opened?.view.episode === item.view.episode" :view="item.view" :cells="item.cells" :status="item.status"
            :marks="marks" :queues="queues" :held="held" :taken="taken" :failed="failed" :busy="busy" :compilations="rows"
            @close="emit('open', null)" @step="step" @switch="emit('member', item.view.episode, $event ? '' : head.key)"
            @member="emit('member', item.view.episode, $event)" @retry-cut="emit('retry', item.view.episode)"
            @retry-failed="emit('retry-failed', item.view.episode)"
            @order="(queue, group, list) => emit('queue-order', item.view.episode, queue, group, list)"
            @allow="(queue, row, allowed) => emit('queue-allow', item.view.episode, queue, row, allowed)"
            @reset="(queue, group) => emit('queue-reset', item.view.episode, queue, group)"
            @same="(queue, groups) => emit('queue-same', item.view.episode, queue, groups)"
          />
        </template>
      </div>
    </div>
  </section>
</template>

<style scoped>
.compilation-letter { display: inline-flex; align-items: center; justify-content: center; min-inline-size: 1.8rem; block-size: 1.8rem;
  border-radius: 0.45rem; background: var(--tone); color: #fff; font-weight: 700; }
.compilation-source { display: inline-flex; align-items: center; gap: 0.35rem; }
</style>
