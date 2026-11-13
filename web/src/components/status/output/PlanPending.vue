<script setup lang="ts">
import { computed, ref } from "vue"
import { t } from "../../../i18n"
import { cancelDelete } from "../../../composables/outputCalls"
import { usePlanStore } from "../../../stores/plan"
import type { components } from "../../../api/schema"

type Command = components["schemas"]["PlanCommand"]

// What the plan of the series holds for the next sync: the commands «Удалить» that wait (each can be taken back) and the
// attempts that failed (the button of the bar resets them). The plan is the core's: it comes with every push.
const props = defineProps<{ seriesId: number }>()

const plan = usePlanStore()
const view = computed(() => plan.forSeries(props.seriesId))
const commands = computed(() => view.value?.commands ?? [])
const failures = computed(() => view.value?.failures?.length ?? 0)
// A failure is «covered» when another source gave what the attempt was for: it costs the series nothing. The rest lack something.
const open = computed(() => (view.value?.failures ?? []).filter((each) => !each.covered).length)
const problems = computed(() => view.value?.problems?.length ?? 0)
const busy = ref(false)

function voiceOf(command: Command): string {
  const asset = (view.value?.assets ?? []).find((each) => each.name === command.name)
  const track = (asset?.tracks ?? []).find((each) => command.dub && each.row.source_id === command.dub.source_id
    && each.row.row_key === command.dub.row_key && (each.row.video ?? "") === (command.dub.video ?? ""))
  return track?.group ?? track?.label ?? ""
}

function text(command: Command): string {
  return command.kind === "track"
    ? t("output.plan.deleteTrack", { voice: voiceOf(command), name: command.name })
    : t("output.plan.deleteVideo", { name: command.name })
}

async function takeBack(command: Command): Promise<void> {
  busy.value = true
  try {
    await cancelDelete(props.seriesId, { kind: command.kind, name: command.name, dub: command.dub ?? null })
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div v-if="commands.length || failures || problems" class="plan-pending">
    <span v-if="failures" class="pill plan-failures" :class="open ? 'pill-danger' : 'pill-secondary'" :title="t('output.plan.failuresHint')">
      <i class="pi pi-exclamation-circle" aria-hidden="true" />{{ open ? t("output.plan.failuresOpen", { count: failures, open }) : t("output.plan.failures", { count: failures }) }}
    </span>
    <span v-if="problems" class="pill pill-warning plan-problems">
      <i class="pi pi-times-circle" aria-hidden="true" />{{ t("output.plan.problems", { count: problems }) }}
    </span>
    <span v-for="command in commands" :key="JSON.stringify(command)" class="pill pill-warning plan-command" :title="t('output.plan.pending')">
      <i class="pi pi-trash" aria-hidden="true" />{{ text(command) }}
      <button type="button" class="plan-cancel" :aria-label="t('output.plan.cancel')" :title="t('output.plan.cancel')" :disabled="busy"
              @click="takeBack(command)"><i class="pi pi-times" aria-hidden="true" /></button>
    </span>
  </div>
</template>

<style scoped>
.plan-pending { display: flex; flex-wrap: wrap; align-items: center; gap: 0.4rem; }
.plan-cancel { all: unset; display: inline-grid; place-items: center; cursor: pointer; padding-inline: 0.15rem; opacity: 0.7; }
.plan-cancel:hover:not(:disabled) { opacity: 1; }
.plan-cancel:disabled { cursor: default; opacity: 0.4; }
.plan-cancel:focus-visible { outline: 0.125rem solid var(--color-blue); border-radius: 4px; }
</style>
