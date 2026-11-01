<script setup lang="ts">
import { computed } from "vue"
import { hasMessage, t, type MessageKey } from "../../i18n"
import VoiceGroupMaker from "../VoiceGroupMaker.vue"
import {
  groupTarget, isOwn, leverState, variantTarget, voiceColumns,
  type EpisodeView, type Lever, type LeverSpec, type LeverState, type Variant,
} from "../../lib/output"

// The levers of one level, drawn from the descriptions the core gives: a column per voice-over group, and in it the
// variants (the source and the player the voice-over can be had from). A lever stands where its target and the shape of
// its value say: a flag of a variant in the variant, a flag of a group in the head of the column, the pick of the
// reference or of the preferred variant as a mark on the variant. Each value shows the level it comes from, and a
// level resets only a value it sets itself.
const props = withDefaults(defineProps<{
  scope: "series" | "season" | "episode"
  view: EpisodeView
  levers: Lever[]
  specs: LeverSpec[]
  busy?: boolean
}>(), { busy: false })

const emit = defineEmits<{
  (e: "set", lever: string, target: string | null, value: unknown): void
  (e: "reset", lever: string, target: string | null): void
}>()

interface Picked {
  group?: string
  source_id: number
  player: string
}

const LEVEL_TEXT: Record<LeverState["level"], MessageKey> = {
  default: "output.level.default",
  series: "output.level.series",
  season: "output.level.season",
  episode: "output.level.episode",
}

const specsBy = (target: LeverSpec["target"], shape: LeverSpec["shape"]) =>
  computed(() => props.specs.filter((spec) => spec.target === target && spec.shape === shape))
const referencePicks = specsBy("none", "reference_pick")
const groupFlags = specsBy("group", "flag")
const groupPicks = specsBy("group", "variant_pick")
const variantFlags = specsBy("variant", "flag")

const columns = computed(() => voiceColumns(props.view))

const groupText = (group: string): string => group || t("output.lever.noGroup")
// A lever the catalog has no title for is named by its own name, so a lever registered later still shows.
const titleOf = (spec: LeverSpec): string => {
  const key = `output.lever.title.${spec.name}`
  return hasMessage(key) ? t(key) : spec.name
}
const variantText = (variant: Variant): string => {
  const source = t("output.lever.source", { id: variant.sourceId })
  return variant.height === null
    ? t("output.variant.label", { player: variant.playerLabel, source })
    : t("output.variant.labelHeight", { player: variant.playerLabel, source, height: variant.height })
}
const levelText = (state: LeverState): string => t(LEVEL_TEXT[state.level])
const stateOf = (spec: LeverSpec, target: string | null): LeverState => leverState(props.levers, props.specs, spec.name, target)
const own = (state: LeverState): boolean => isOwn(state, props.scope)
const pickedFlag = (event: Event): boolean => (event.target as HTMLInputElement).checked
// Taking and participation are on by default, so only an explicit false switches them off.
const flagOf = (spec: LeverSpec, target: string): boolean => stateOf(spec, target).value !== false

// A value arrives as the core stores it: only an object of the right shape is read as a pick.
function asPick(value: unknown): Picked | null {
  const found = value as Partial<Picked> | null
  return found && typeof found.source_id === "number" && typeof found.player === "string"
    ? { source_id: found.source_id, player: found.player, ...(typeof found.group === "string" ? { group: found.group } : {}) }
    : null
}

const variantTargetOf = (variant: Variant): string => variantTarget(variant.group, variant.sourceId, variant.player)
const isPickOf = (pick: Picked | null, variant: Variant, withGroup: boolean): boolean =>
  pick !== null && pick.source_id === variant.sourceId && pick.player === variant.player
  && (!withGroup || pick.group === variant.group)

const referencePick = (spec: LeverSpec): Picked | null => asPick(stateOf(spec, null).value)
const isReference = (spec: LeverSpec, variant: Variant): boolean => isPickOf(referencePick(spec), variant, true)
const isPreferred = (spec: LeverSpec, variant: Variant): boolean =>
  isPickOf(asPick(stateOf(spec, groupTarget(variant.group)).value), variant, false)
const preferredOf = (spec: LeverSpec, group: string): Picked | null => asPick(stateOf(spec, groupTarget(group)).value)

function referenceText(spec: LeverSpec): string {
  const pick = referencePick(spec)
  if (pick === null) return t("output.lever.auto")
  const variant = columns.value.flatMap((column) => column.variants).find((each) => isPickOf(pick, each, true))
  return variant
    ? t("output.lever.referenceLabel", { group: groupText(variant.group), player: variantText(variant) })
    : t("output.lever.missing", { label: `${groupText(pick.group ?? "")} — ${pick.player}` })
}

function preferredText(spec: LeverSpec, group: string): string {
  const pick = preferredOf(spec, group)
  if (pick === null) return t("output.lever.auto")
  const variant = columns.value.find((column) => column.group === group)?.variants.find((each) => isPickOf(pick, each, false))
  return variant ? variantText(variant) : t("output.lever.missing", { label: pick.player })
}

const pickReference = (spec: LeverSpec, variant: Variant): void =>
  emit("set", spec.name, null, { group: variant.group, source_id: variant.sourceId, player: variant.player })
const preferVariant = (spec: LeverSpec, variant: Variant): void =>
  emit("set", spec.name, groupTarget(variant.group), { source_id: variant.sourceId, player: variant.player })
</script>

<template>
  <div class="output-levers stack">
    <section v-for="spec in referencePicks" :key="spec.name" class="lever-block lever-reference">
      <h3 class="lever-title">{{ titleOf(spec) }}</h3>
      <div class="cluster">
        <span class="lever-value">{{ referenceText(spec) }}</span>
        <span class="lever-level">{{ levelText(stateOf(spec, null)) }}</span>
        <button
          v-if="referencePick(spec) !== null" type="button" class="lever-auto" :disabled="busy"
          @click="emit('set', spec.name, null, null)"
        >{{ t("output.lever.toAuto") }}</button>
        <button
          v-if="own(stateOf(spec, null))" type="button" class="lever-reset" :disabled="busy"
          @click="emit('reset', spec.name, null)"
        >{{ t("output.lever.reset") }}</button>
      </div>
    </section>

    <div class="voice-grid">
      <article v-for="column in columns" :key="column.group" class="voice-column lever-block stack" :data-group="column.group">
        <header class="voice-head stack">
          <h4 class="voice-name">{{ groupText(column.group) }}</h4>
          <VoiceGroupMaker v-if="column.groupless" :names="column.names" :disabled="busy" />
          <div v-for="spec in groupFlags" :key="spec.name" class="cluster lever-row">
            <label class="cluster lever-pick">
              <input
                type="checkbox" :checked="flagOf(spec, groupTarget(column.group))" :disabled="busy"
                @change="emit('set', spec.name, groupTarget(column.group), pickedFlag($event))"
              />
              <span>{{ titleOf(spec) }}</span>
            </label>
            <span class="lever-level">{{ levelText(stateOf(spec, groupTarget(column.group))) }}</span>
            <button
              v-if="own(stateOf(spec, groupTarget(column.group)))" type="button" class="lever-reset" :disabled="busy"
              @click="emit('reset', spec.name, groupTarget(column.group))"
            >{{ t("output.lever.reset") }}</button>
          </div>
          <div v-for="spec in groupPicks" :key="spec.name" class="cluster lever-row">
            <span class="lever-value">{{ titleOf(spec) }}: {{ preferredText(spec, column.group) }}</span>
            <span class="lever-level">{{ levelText(stateOf(spec, groupTarget(column.group))) }}</span>
            <button
              v-if="preferredOf(spec, column.group) !== null" type="button" class="lever-auto" :disabled="busy"
              @click="emit('set', spec.name, groupTarget(column.group), null)"
            >{{ t("output.lever.toAuto") }}</button>
            <button
              v-if="own(stateOf(spec, groupTarget(column.group)))" type="button" class="lever-reset" :disabled="busy"
              @click="emit('reset', spec.name, groupTarget(column.group))"
            >{{ t("output.lever.reset") }}</button>
          </div>
        </header>
        <ul class="voice-variants stack">
          <li
            v-for="variant in column.variants" :key="variantTargetOf(variant)" class="variant stack"
            :class="{ 'is-reference': referencePicks.some((spec) => isReference(spec, variant)) }"
          >
            <span class="variant-name">{{ variantText(variant) }}</span>
            <div class="cluster lever-row">
              <button
                v-for="spec in referencePicks" :key="`ref-${spec.name}`" type="button" class="variant-mark variant-reference"
                :aria-pressed="isReference(spec, variant)" :disabled="busy || !variant.referable"
                @click="pickReference(spec, variant)"
              >{{ t("output.variant.reference") }}</button>
              <button
                v-for="spec in groupPicks" :key="`prefer-${spec.name}`" type="button" class="variant-mark variant-prefer"
                :aria-pressed="isPreferred(spec, variant)" :disabled="busy || !variant.playable"
                @click="preferVariant(spec, variant)"
              >{{ t("output.variant.prefer") }}</button>
            </div>
            <div v-for="spec in variantFlags" :key="spec.name" class="cluster lever-row">
              <label class="cluster lever-pick">
                <input
                  type="checkbox" :checked="flagOf(spec, variantTargetOf(variant))" :disabled="busy"
                  @change="emit('set', spec.name, variantTargetOf(variant), pickedFlag($event))"
                />
                <span>{{ titleOf(spec) }}</span>
              </label>
              <span class="lever-level">{{ levelText(stateOf(spec, variantTargetOf(variant))) }}</span>
              <button
                v-if="own(stateOf(spec, variantTargetOf(variant)))" type="button" class="lever-reset" :disabled="busy"
                @click="emit('reset', spec.name, variantTargetOf(variant))"
              >{{ t("output.lever.reset") }}</button>
            </div>
          </li>
        </ul>
      </article>
    </div>
  </div>
</template>

<style scoped>
.lever-block {
  padding: var(--space); border: var(--border-width) solid var(--border-color);
  border-radius: var(--border-radius); background: var(--bg-white);
}
.lever-title, .voice-name { margin: 0; font-size: 1em; }
.voice-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, var(--measure)), 1fr)); gap: var(--space); }
.voice-column { min-inline-size: 0; }
.voice-variants { list-style: none; margin: 0; padding: 0; }
.variant { padding: 0.25em; border-radius: var(--border-radius); }
.variant.is-reference { background: var(--bg-light); }
.variant-name, .lever-value, .lever-pick > span { overflow-wrap: anywhere; }
.lever-pick { cursor: pointer; }
.lever-level { color: var(--text-muted); font-size: smaller; }
.variant-mark[aria-pressed="true"] { font-weight: bold; }
.lever-reset, .lever-auto {
  padding: 0; border: none; background: none; cursor: pointer;
  color: var(--color-blue); text-decoration: underline;
}
.lever-block :disabled { cursor: default; opacity: 0.5; }
</style>
