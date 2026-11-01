<script setup lang="ts">
import { computed, ref } from "vue"
import Button from "primevue/button"
import { useSeriesOutput } from "../../composables/useSeriesOutput"
import { t } from "../../i18n"
import { isDeviation, type Scope } from "../../lib/output"
import OutputEpisode from "./OutputEpisode.vue"
import OutputLevel from "./OutputLevel.vue"

// The "Output" section of a video series: what each episode takes and why, its voice-overs, and the levers of the
// series, of each season and of each episode. The data is the core's; every change goes to it and comes back as events.
const props = defineProps<{ seriesId: number; seriesName: string }>()

const output = useSeriesOutput(props.seriesId, "video")
const onlyDeviations = ref(false)
// A refusal of the core leaves the controls as the user set them; rendering them anew brings back what the core holds.
const renders = ref(0)

const seriesViews = computed(() => output.seasons.value.flatMap((season) => season.episodes))
const levelLevers = (season: number | null) =>
  output.levels.value.find((level) => (level.season ?? null) === season)?.levers ?? []
const shownSeasons = computed(() => output.seasons.value
  .map((season) => ({ ...season, shown: onlyDeviations.value ? season.episodes.filter(isDeviation) : season.episodes }))
  .filter((season) => season.shown.length > 0))

async function changed(run: Promise<string | null>): Promise<void> {
  if ((await run) !== null) renders.value += 1
}

const setLever = (scope: Scope, lever: string, target: string | null, value: unknown) =>
  changed(output.set(scope, lever, target, value))
const resetLever = (scope: Scope, lever: string, target: string | null) =>
  changed(output.reset(scope, lever, target))
const switchEpisode = (season: number, episode: number, off: boolean) => changed(output.switchTo(season, episode, off))
const switchSeason = (season: number, off: boolean) => changed(output.switchTo(season, null, off))
</script>

<template>
  <div class="status-output stack">
    <div class="cluster output-bar">
      <label class="cluster output-filter">
        <input v-model="onlyDeviations" type="checkbox" />
        <span>{{ t("output.filter.deviations") }}</span>
      </label>
      <Button
        :label="t('output.sync')" :icon="output.syncing.value ? 'pi pi-spin pi-spinner' : 'pi pi-refresh'"
        size="small" :disabled="output.syncing.value" @click="output.sync()"
      />
      <Button
        :label="t('output.reprocess')" :icon="output.reprocessing.value ? 'pi pi-spin pi-spinner' : 'pi pi-pencil'"
        size="small" severity="warn" :disabled="output.reprocessing.value" @click="output.reprocess()"
      />
      <Button
        :label="t('output.adopt')" :icon="output.adopting.value ? 'pi pi-spin pi-spinner' : 'pi pi-sparkles'"
        size="small" severity="info" :disabled="output.adopting.value" @click="output.adopt()"
      />
    </div>

    <p v-if="output.failure.value" class="output-failure" role="alert">{{ output.failure.value }}</p>

    <div v-if="output.loading.value && seriesViews.length === 0" class="empty-state">{{ t("output.loading") }}</div>
    <div v-else-if="seriesViews.length === 0" class="empty-state">{{ t("output.empty") }}</div>
    <OutputLevel
      v-else :key="renders" scope="series" :title="seriesName" :views="seriesViews" :levers="levelLevers(null)"
      :busy="output.busy.value" @set="setLever" @reset="resetLever"
    >
      <OutputLevel
        v-for="season in shownSeasons" :key="season.season" scope="season" :season="season.season"
        :title="t('output.season', { n: season.season })" :views="season.episodes" :levers="levelLevers(season.season)"
        :busy="output.busy.value" @set="setLever" @reset="resetLever" @switch="switchSeason"
      >
        <OutputEpisode
          v-for="view in season.shown" :key="view.episode" :view="view" :busy="output.busy.value"
          @set="setLever" @reset="resetLever" @switch="switchEpisode"
        />
      </OutputLevel>
    </OutputLevel>
  </div>
</template>

<style scoped>
.output-bar { align-items: center; }
.output-failure { margin: 0; color: var(--color-red-1); overflow-wrap: anywhere; }
</style>
