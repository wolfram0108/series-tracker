<script setup lang="ts">
import { computed } from "vue"
import type { Queues } from "../../../composables/outputCalls"
import { useCardGrid } from "../../../composables/useCardGrid"
import { t } from "../../../i18n"
import {
  badgeOf, cardStatus, cutOf, cutState, shareOf, type CompilationView, type CutItem, type CutState,
} from "../../../lib/compilations"
import { trackCells, type EpisodeView, type QueueName, type QueueRow, type SourceMark as Mark } from "../../../lib/output"
import type { CompilationRow } from "./CompilationTable.vue"
import EpisodeCard from "./EpisodeCard.vue"
import EpisodeDetail from "./EpisodeDetail.vue"
import PowerSwitch from "./PowerSwitch.vue"
import SourceMark from "./SourceMark.vue"

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
  mode?: "cards" | "tiles"
  opened?: number | null
  queues?: Queues
  busy?: boolean
}>(), { mode: "cards", opened: null, busy: false })

const emit = defineEmits<{
  (e: "open", episode: number | null): void
  (e: "settings"): void
  (e: "switch", off: boolean): void
  (e: "member", episode: number, key: string): void
  (e: "retry", episode: number): void
  (e: "queue-order", episode: number, queue: QueueName, group: string | null, rows: QueueRow[]): void
  (e: "queue-allow", episode: number, queue: QueueName, row: QueueRow, allowed: boolean): void
  (e: "queue-reset", episode: number, queue: QueueName, group: string | null): void
}>()

const badge = computed(() => badgeOf(props.view))
const head = computed(() => props.view.ref)
const range = (view: CompilationView) => `${view.ref.start}–${view.ref.end}`
const playerOf = (view: CompilationView) => props.labels[`${view.ref.source_id}:${view.ref.player}`] ?? view.ref.player
const byEpisode = computed(() => new Map(props.episodes.map((view) => [view.episode, view] as const)))

const items = computed(() => (props.view.episodes ?? []).flatMap((each) => {
  const view = byEpisode.value.get(each.episode)
  if (!view) return []
  const cut = each.member ? cutOf(props.cuts, props.view.season, each.episode) : undefined
  const status = cardStatus(each.member, cut)
  const elsewhere = each.elsewhere ? props.season.find((other) => other.ref.key === each.elsewhere) : undefined
  const note = each.member ? undefined : elsewhere
    ? t("output.compilation.absence.elsewhere", { letter: badgeOf(elsewhere).letter, range: range(elsewhere) })
    : t(`output.compilation.absence.${each.absence ?? "own"}`)
  const fits = props.view.take && (each.member || each.absence === "elsewhere" || each.absence === "own")
  return [{ view, each, cut, status, note, fits, cells: trackCells(view, []) }]
}))

const cutCounts = computed(() => {
  const counts: Record<CutState, number> = { wait: 0, down: 0, align: 0, ready: 0, error: 0 }
  for (const item of items.value) {
    if (item.each.member) counts[cutState(item.cut) ?? "ready"] += 1
  }
  return counts
})
const taken = computed(() => items.value.filter((item) => item.each.member).length)
const share = (count: number, all: number): string => `${all === 0 ? 0 : (count * 100) / all}%`
const cutText = computed(() => (Object.entries(cutCounts.value) as [CutState, number][])
  .filter(([, count]) => count > 0).map(([state, count]) => t(`output.cut.meter.${state}`, { n: count })).join(" · "))

const { grid, ruler, columns } = useCardGrid(() => props.mode, () => 2, () => items.value.length)

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
    const member = !!each?.member
    const cut = member ? cutOf(props.cuts, view.season, episode) : undefined
    return {
      view, badge: badgeOf(view), member, player: playerOf(view), cut: cutState(cut), share: shareOf(cut),
      fits: view.take && (member || each?.absence === "elsewhere" || each?.absence === "own"),
    }
  })
})
</script>

<template>
  <section class="season-block modern-fieldset compilation-block" :data-compilation="head.key">
    <header class="season-head">
      <span class="compilation-letter" :style="{ '--tone': `var(--compilation-${badge.color})` }">{{ badge.letter }}</span>
      <span class="season-title">{{ t("output.compilation.title", { letter: badge.letter, range: range(view) }) }}</span>
      <span class="season-range compilation-source">
        <SourceMark :mark="marks[head.source_id]" />{{ marks[head.source_id]?.place }}
        · {{ t("output.compilation.what", { player: playerOf(view), height: head.height ?? "—", count: (view.episodes ?? []).length }) }}
      </span>
      <div class="season-meters">
        <div class="meter">
          <span class="meter-name">{{ t("output.meter.episodes") }}</span>
          <span class="meter-bar"><span class="is-align" :style="{ inlineSize: share(taken, items.length) }" /></span>
          <span class="meter-text">{{ t("output.meter.taken", { n: taken, total: items.length }) }}</span>
        </div>
        <div v-if="taken" class="meter">
          <span class="meter-name">{{ t("output.meter.cut") }}</span>
          <span class="meter-bar">
            <span v-for="(count, state) in cutCounts" :key="state" :class="`is-${state === 'ready' ? 'done' : state}`"
                  :style="{ inlineSize: share(count, taken) }" />
          </span>
          <span class="meter-text">{{ cutText }}</span>
        </div>
      </div>
      <button type="button" class="action-btn btn-status season-settings" @click="emit('settings')">
        <i class="pi pi-sliders-h" />{{ t("output.compilation.settings") }}
      </button>
      <PowerSwitch :on="view.take" :busy="busy" :label="t(view.take ? 'output.compilation.disable' : 'output.compilation.enable')"
                   @toggle="emit('switch', !$event)" />
    </header>
    <div class="season-body">
      <div ref="grid" class="season-grid" :class="`is-${mode}`" :style="columns">
        <span ref="ruler" class="grid-ruler" aria-hidden="true" />
        <template v-for="item in items" :key="item.view.episode">
          <EpisodeCard
            v-if="mode === 'cards'" :view="item.view" :cells="item.cells" :status="item.status" :marks="marks"
            :open="opened?.view.episode === item.view.episode" :busy="busy || !item.fits"
            :badge="item.each.member ? badge : undefined" :note="item.note"
            :switch-label="t(item.each.member ? 'output.compilation.leave' : 'output.compilation.take')"
            @open="emit('open', opened?.view.episode === item.view.episode ? null : item.view.episode)"
            @switch="emit('member', item.view.episode, $event ? '' : head.key)"
          />
          <button
            v-else type="button" class="episode-tile" :data-episode="item.view.episode"
            :class="[`is-${item.status}`, { 'is-open': opened?.view.episode === item.view.episode }]"
            :title="item.note" @click="emit('open', opened?.view.episode === item.view.episode ? null : item.view.episode)"
          >{{ item.view.episode }}</button>
          <EpisodeDetail
            v-if="opened?.view.episode === item.view.episode" :view="item.view" :cells="item.cells" :status="item.status"
            :marks="marks" :queues="queues" :busy="busy" :compilations="rows"
            @close="emit('open', null)" @step="step" @switch="emit('member', item.view.episode, $event ? '' : head.key)"
            @member="emit('member', item.view.episode, $event)" @retry-cut="emit('retry', item.view.episode)"
            @order="(queue, group, list) => emit('queue-order', item.view.episode, queue, group, list)"
            @allow="(queue, row, allowed) => emit('queue-allow', item.view.episode, queue, row, allowed)"
            @reset="(queue, group) => emit('queue-reset', item.view.episode, queue, group)"
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
