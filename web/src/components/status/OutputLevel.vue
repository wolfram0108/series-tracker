<script setup lang="ts">
import { computed, ref } from "vue"
import { t } from "../../i18n"
import { scopeView, type EpisodeView, type Lever, type Scope } from "../../lib/output"
import OutputLevers from "./OutputLevers.vue"

// A header of one level, with its episodes in the slot; the levers and the folding are local, since they change no data.
const props = withDefaults(defineProps<{
  scope: "series" | "season"
  title: string
  views: EpisodeView[]
  levers: Lever[]
  season?: number
  busy?: boolean
}>(), { busy: false })

const emit = defineEmits<{
  (e: "set", scope: Scope, lever: string, target: string | null, value: unknown): void
  (e: "reset", scope: Scope, lever: string, target: string | null): void
}>()

// Named apart from the levers prop, which a ref of the same name would shadow.
const leversOpen = ref(false)
const folded = ref(false)
const pooled = computed(() => scopeView(props.views))

// A season is named by its number; the whole series is named by no field at all.
function levelScope(): Scope {
  return props.scope === "season" ? { season: props.season } : {}
}

function forwardSet(lever: string, target: string | null, value: unknown): void {
  emit("set", levelScope(), lever, target, value)
}

function forwardReset(lever: string, target: string | null): void {
  emit("reset", levelScope(), lever, target)
}
</script>

<template>
  <section class="output-level stack" :data-scope="scope" :data-season="scope === 'season' ? season : undefined">
    <header class="cluster">
      <h2 class="level-title">{{ title }}</h2>
      <span class="level-count">{{ t("output.episodes", { n: views.length }) }}</span>
      <button
        type="button" class="level-toggle level-levers" :aria-expanded="leversOpen"
        @click="leversOpen = !leversOpen"
      >{{ t(leversOpen ? "output.levers.hide" : "output.levers.show") }}</button>
      <button
        type="button" class="level-toggle level-fold" :aria-expanded="!folded"
        @click="folded = !folded"
      >{{ t(folded ? "output.unfold" : "output.fold") }}</button>
    </header>

    <OutputLevers
      v-if="leversOpen" :scope="scope" :view="pooled" :levers="levers" :busy="busy"
      @set="forwardSet" @reset="forwardReset"
    />

    <div v-if="!folded" class="level-episodes stack">
      <slot />
    </div>
  </section>
</template>

<style scoped>
.level-title { margin: 0; font-size: 1em; overflow-wrap: anywhere; }
.level-count { color: var(--text-muted); font-size: smaller; }
.level-toggle {
  padding: 0; border: none; background: none; cursor: pointer;
  color: var(--color-blue); text-decoration: underline;
}
</style>
