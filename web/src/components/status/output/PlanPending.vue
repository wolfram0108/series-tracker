<script setup lang="ts">
import { computed, ref } from "vue"
import Button from "primevue/button"
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
  <div v-if="commands.length || failures" class="output-bar plan-pending">
    <span v-if="failures" class="plan-failures">{{ t("output.plan.failures", { count: failures }) }}</span>
    <template v-if="commands.length">
      <span class="plan-pending-name">{{ t("output.plan.pending") }}</span>
      <span v-for="command in commands" :key="JSON.stringify(command)" class="plan-command">
        {{ text(command) }}
        <Button :label="t('output.plan.cancel')" size="small" severity="secondary" text :disabled="busy"
                @click="takeBack(command)" />
      </span>
    </template>
  </div>
</template>
