<script setup lang="ts">
import { computed } from "vue"
import { t, type MessageKey } from "../../i18n"
import {
  LEVER, groupTarget, isOwn, leverState, playerOptions, playerTarget, referenceOptions, sourcePlayers, voiceGroups,
  type EpisodeView, type Lever, type LeverState, type PlayerOption,
} from "../../lib/output"

// The levers of one level: each value shows the level it comes from, and a level resets only a value it sets itself.
const props = withDefaults(defineProps<{
  scope: "series" | "season" | "episode"
  view: EpisodeView
  levers: Lever[]
  busy?: boolean
}>(), { busy: false })

const emit = defineEmits<{
  (e: "set", lever: string, target: string | null, value: unknown): void
  (e: "reset", lever: string, target: string | null): void
}>()

// A native select holds strings only, so auto is the empty string. Choices are found by key and emit their own data,
// never a key parsed back, because names may hold any character.
const AUTO = ""

interface Choice<T> {
  value: string
  label: string
  data: T
}

interface ReferenceValue {
  group: string
  source_id: number
  player: string
}

interface PlayerValue {
  source_id: number
  player: string
}

const LEVEL_TEXT: Record<LeverState["level"], MessageKey> = {
  default: "output.level.default",
  series: "output.level.series",
  season: "output.level.season",
  episode: "output.level.episode",
}

const referenceKey = (value: ReferenceValue): string => `${value.group}|${value.source_id}|${value.player}`
const playerKey = (value: PlayerValue): string => `${value.source_id}|${value.player}`
const groupText = (group: string): string => group || t("output.lever.noGroup")
const playerSourceText = (player: string, sourceId: number): string =>
  t("output.lever.playerSource", { player, source: t("output.lever.source", { id: sourceId }) })
const allowTarget = (option: PlayerOption): string => playerTarget(option.sourceId, option.player)

// A value arrives as the core stores it: only an object of the right shape is read as a choice.
function asReference(value: unknown): ReferenceValue | null {
  const found = value as Partial<ReferenceValue> | null
  return found && typeof found.group === "string" && typeof found.source_id === "number" && typeof found.player === "string"
    ? { group: found.group, source_id: found.source_id, player: found.player }
    : null
}

function asPlayer(value: unknown): PlayerValue | null {
  const found = value as Partial<PlayerValue> | null
  return found && typeof found.source_id === "number" && typeof found.player === "string"
    ? { source_id: found.source_id, player: found.player }
    : null
}

const stateOf = (lever: string, target: string | null): LeverState => leverState(props.levers, lever, target)
// Taking and participation are on by default, so only an explicit false switches them off.
const flagOf = (lever: string, target: string): boolean => stateOf(lever, target).value !== false
const levelText = (state: LeverState): string => t(LEVEL_TEXT[state.level])
const pickedText = (event: Event): string => (event.target as HTMLSelectElement).value
const pickedFlag = (event: Event): boolean => (event.target as HTMLInputElement).checked

const players = computed(() => sourcePlayers(props.view))
const groups = computed(() => voiceGroups(props.view))

// A chosen value that the options no longer offer stays listed and marked: the select must not show another choice.
function withMissing<T>(
  choices: Choice<T>[], chosen: T | null, keyOf: (value: T) => string, labelOf: (value: T) => string,
): Choice<T>[] {
  if (!chosen || choices.some((choice) => choice.value === keyOf(chosen))) return choices
  return [...choices, { value: keyOf(chosen), label: t("output.lever.missing", { label: labelOf(chosen) }), data: chosen }]
}

function playerLabelOf(sourceId: number, player: string): string {
  return players.value.find((option) => option.sourceId === sourceId && option.player === player)?.playerLabel ?? player
}

const referenceState = computed(() => stateOf(LEVER.choice, null))
const chosenReference = computed(() => asReference(referenceState.value.value))
const chosenReferenceKey = computed(() => (chosenReference.value ? referenceKey(chosenReference.value) : AUTO))

const referenceChoices = computed(() => withMissing<ReferenceValue>(
  referenceOptions(props.view).map((option) => ({
    value: referenceKey({ group: option.group, source_id: option.sourceId, player: option.player }),
    label: option.height === null
      ? t("output.lever.referenceLabel", { group: groupText(option.group), player: option.playerLabel })
      : t("output.lever.referenceLabelHeight", {
        group: groupText(option.group), player: option.playerLabel, height: option.height,
      }),
    data: { group: option.group, source_id: option.sourceId, player: option.player },
  })),
  chosenReference.value,
  referenceKey,
  (value) => t("output.lever.referenceLabel", { group: groupText(value.group), player: playerLabelOf(value.source_id, value.player) }),
))

function playerChoicesOf(group: string): Choice<PlayerValue>[] {
  return withMissing<PlayerValue>(
    playerOptions(props.view, group).map((option) => ({
      value: playerKey({ source_id: option.sourceId, player: option.player }),
      label: playerSourceText(option.playerLabel, option.sourceId),
      data: { source_id: option.sourceId, player: option.player },
    })),
    asPlayer(stateOf(LEVER.player, groupTarget(group)).value),
    playerKey,
    (value) => playerSourceText(playerLabelOf(value.source_id, value.player), value.source_id),
  )
}

function playerValueOf(group: string): string {
  const chosen = asPlayer(stateOf(LEVER.player, groupTarget(group)).value)
  return chosen ? playerKey(chosen) : AUTO
}

function pickReference(value: string): void {
  if (value === AUTO) {
    emit("set", LEVER.choice, null, null)
    return
  }
  const choice = referenceChoices.value.find((each) => each.value === value)
  if (choice) emit("set", LEVER.choice, null, choice.data)
}

function pickPlayer(group: string, value: string): void {
  const target = groupTarget(group)
  if (value === AUTO) {
    emit("set", LEVER.player, target, null)
    return
  }
  const choice = playerChoicesOf(group).find((each) => each.value === value)
  if (choice) emit("set", LEVER.player, target, choice.data)
}
</script>

<template>
  <div class="output-levers stack">
    <section v-if="referenceChoices.length" class="lever-block lever-choice stack">
      <h3 class="lever-title">{{ t("output.lever.choice") }}</h3>
      <div class="cluster">
        <select
          class="lever-select" :value="chosenReferenceKey" :disabled="busy" :aria-label="t('output.lever.choice')"
          @change="pickReference(pickedText($event))"
        >
          <option value="">{{ t("output.lever.auto") }}</option>
          <option v-for="choice in referenceChoices" :key="choice.value" :value="choice.value">{{ choice.label }}</option>
        </select>
        <span class="lever-level">{{ levelText(referenceState) }}</span>
        <button
          v-if="isOwn(referenceState, scope)" type="button" class="lever-reset" :disabled="busy"
          @click="emit('reset', LEVER.choice, null)"
        >{{ t("output.lever.reset") }}</button>
      </div>
    </section>

    <section v-if="players.length" class="lever-block lever-allow stack">
      <h3 class="lever-title">{{ t("output.lever.allow") }}</h3>
      <ul class="lever-list stack">
        <li v-for="option in players" :key="allowTarget(option)" class="cluster">
          <label class="cluster lever-pick">
            <input
              type="checkbox" :checked="flagOf(LEVER.allow, allowTarget(option))" :disabled="busy"
              @change="emit('set', LEVER.allow, allowTarget(option), pickedFlag($event))"
            />
            <span>{{ playerSourceText(option.playerLabel, option.sourceId) }}</span>
          </label>
          <span class="lever-level">{{ levelText(stateOf(LEVER.allow, allowTarget(option))) }}</span>
          <button
            v-if="isOwn(stateOf(LEVER.allow, allowTarget(option)), scope)" type="button" class="lever-reset" :disabled="busy"
            @click="emit('reset', LEVER.allow, allowTarget(option))"
          >{{ t("output.lever.reset") }}</button>
        </li>
      </ul>
    </section>

    <section v-if="groups.length" class="lever-block lever-take stack">
      <h3 class="lever-title">{{ t("output.lever.take") }}</h3>
      <ul class="lever-list stack">
        <li v-for="group in groups" :key="group" class="cluster">
          <label class="cluster lever-pick">
            <input
              type="checkbox" :checked="flagOf(LEVER.take, groupTarget(group))" :disabled="busy"
              @change="emit('set', LEVER.take, groupTarget(group), pickedFlag($event))"
            />
            <span>{{ groupText(group) }}</span>
          </label>
          <span class="lever-level">{{ levelText(stateOf(LEVER.take, groupTarget(group))) }}</span>
          <button
            v-if="isOwn(stateOf(LEVER.take, groupTarget(group)), scope)" type="button" class="lever-reset" :disabled="busy"
            @click="emit('reset', LEVER.take, groupTarget(group))"
          >{{ t("output.lever.reset") }}</button>
        </li>
      </ul>
    </section>

    <section v-if="groups.length" class="lever-block lever-player stack">
      <h3 class="lever-title">{{ t("output.lever.player") }}</h3>
      <ul class="lever-list stack">
        <li v-for="group in groups" :key="group" class="cluster">
          <span class="lever-group">{{ groupText(group) }}</span>
          <select
            class="lever-select" :value="playerValueOf(group)" :disabled="busy" :aria-label="groupText(group)"
            @change="pickPlayer(group, pickedText($event))"
          >
            <option value="">{{ t("output.lever.auto") }}</option>
            <option v-for="choice in playerChoicesOf(group)" :key="choice.value" :value="choice.value">{{ choice.label }}</option>
          </select>
          <span class="lever-level">{{ levelText(stateOf(LEVER.player, groupTarget(group))) }}</span>
          <button
            v-if="isOwn(stateOf(LEVER.player, groupTarget(group)), scope)" type="button" class="lever-reset" :disabled="busy"
            @click="emit('reset', LEVER.player, groupTarget(group))"
          >{{ t("output.lever.reset") }}</button>
        </li>
      </ul>
    </section>
  </div>
</template>

<style scoped>
.lever-block {
  padding: var(--space); border: var(--border-width) solid var(--border-color);
  border-radius: var(--border-radius); background: var(--bg-white);
}
.lever-title { margin: 0; font-size: 1em; }
.lever-list { list-style: none; margin: 0; padding: 0; }
.lever-pick { cursor: pointer; }
.lever-group, .lever-pick > span { overflow-wrap: anywhere; }
.lever-select { flex: 1 1 var(--measure); min-inline-size: 0; max-inline-size: 100%; }
.lever-level { color: var(--text-muted); font-size: smaller; }
.lever-reset {
  padding: 0; border: none; background: none; cursor: pointer;
  color: var(--color-blue); text-decoration: underline;
}
.lever-block :disabled { cursor: default; opacity: 0.5; }
</style>
