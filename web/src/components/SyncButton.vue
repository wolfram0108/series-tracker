<script setup lang="ts">
import { computed, inject } from "vue"
import { t } from "../i18n"
import { SYNC_RUN, type SyncScope } from "../composables/syncRun"
import FoldButton from "./FoldButton.vue"

// The button of the sync of a place — the series (no scope), a season, an episode of it. One place for both actions: while the
// series is synced the button is the one that stops the pass, whichever place it stands at. `name` gives its classes
// (`<name>-sync`, `<name>-stop`). It is drawn only where a pass is offered (the section «Выход» provides it).
const props = defineProps<{ name: string; scope?: SyncScope; fold?: "sm" | "md" | "lg" | "xl" }>()
const run = inject(SYNC_RUN, null)
const hint = computed(() => props.scope === undefined ? undefined
  : t(props.scope.episode === undefined ? "output.season.syncHint" : "output.episode.syncHint"))
</script>

<template>
  <template v-if="run">
    <FoldButton v-if="run.running.value" :label="t('output.stop')" :hint="t('sync.stop')" icon="pi pi-stop-circle" severity="danger"
                :fold="fold" :class="`${name}-stop`" :disabled="run.stopping.value" @click="run.stop()" />
    <FoldButton v-else :label="t('output.sync')" :hint="hint" icon="pi pi-refresh" severity="warn" :fold="fold" :class="`${name}-sync`"
                :disabled="!run.allowed.value" @click="run.sync(scope)" />
  </template>
</template>
