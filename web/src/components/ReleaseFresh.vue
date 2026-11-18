<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue"
import FreshMark from "./FreshMark.vue"
import { fetchReleases } from "../composables/torrentCalls"
import { useSSE } from "../composables/useSSE"
import type { ReleaseView } from "../lib/torrent"

// How current the release of a torrent source is: when its page was last checked on the tracker and until the next check
// of the schedule. Asked once, then carried on by the news of the release; a source with no watched release shows nothing.
const props = defineProps<{ url: string }>()
const kept = ref<ReleaseView[]>([])
const watched = computed(() => kept.value.find((each) => each.watched) ?? null)

const sse = useSSE()
const off = sse.on("torrent_release_update", (data) => {
  const view = data as ReleaseView
  if (view.url !== props.url && !kept.value.some((each) => each.url === view.url)) return
  kept.value = [view, ...kept.value.filter((each) => each.release !== view.release)]
})
onMounted(async () => {
  const answer = await fetchReleases(props.url)
  if (answer.ok) kept.value = answer.data.releases
})
onUnmounted(off)
</script>

<template>
  <FreshMark v-if="watched" :at="watched.checked_at ?? null" :until="watched.fresh_until ?? null" />
</template>
