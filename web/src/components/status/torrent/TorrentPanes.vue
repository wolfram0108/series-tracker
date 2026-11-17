<script lang="ts">
import { ref } from "vue"

/** Where the switch of the panes stands: at the left the right pane takes the whole width, at the right the left one does,
 *  in the middle both share it. One for every tab of the editor: a tab chosen keeps the width the user gave the panes. */
export type PanesAt = "left" | "middle" | "right"
const at = ref<PanesAt>("middle")
</script>

<script setup lang="ts">
import { computed } from "vue"
import { t } from "../../../i18n"

// The two panes of a tab of the editor (what the release holds, where it goes) in one frame with one head: the head of each
// pane over it, the switch of their width in the middle.
const STOPS: PanesAt[] = ["left", "middle", "right"]
const showLeft = computed(() => at.value !== "left")
const showRight = computed(() => at.value !== "right")

function step(by: -1 | 1): void {
  at.value = STOPS[Math.min(2, Math.max(0, STOPS.indexOf(at.value) + by))]
}
</script>

<template>
  <div class="torrent-panes" :class="`is-${at}`">
    <div class="torrent-panes-head">
      <div class="torrent-pane-head is-left" :class="{ 'is-off': !showLeft }"><slot name="left-head" /></div>
      <div class="torrent-panes-switch" role="radiogroup" :aria-label="t('torrent.panes.switch')"
           @keydown.left.prevent="step(-1)" @keydown.right.prevent="step(1)">
        <button v-for="stop in STOPS" :key="stop" type="button" role="radio" class="torrent-panes-stop" :class="`at-${stop}`"
                :aria-checked="at === stop" :tabindex="at === stop ? 0 : -1" :title="t(`torrent.panes.${stop}`)"
                :aria-label="t(`torrent.panes.${stop}`)" @click="at = stop" />
        <span class="torrent-panes-thumb" aria-hidden="true" />
      </div>
      <div class="torrent-pane-head is-right" :class="{ 'is-off': !showRight }"><slot name="right-head" /></div>
    </div>
    <div class="torrent-panes-body">
      <section v-show="showLeft" class="torrent-pane"><slot name="left" /></section>
      <section v-show="showRight" class="torrent-pane"><slot name="right" /></section>
    </div>
  </div>
</template>
