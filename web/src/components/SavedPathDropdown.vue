<script setup lang="ts">
import { computed, ref } from "vue"
import { api } from "../api/client"
import PickDropdown from "./PickDropdown.vue"
import { t } from "../i18n"

// The choice of a saved path (Settings → Debug → saved paths) in the group of the field of a path. It keeps no choice:
// a click on a path emits the value ready for the field. A catalog name («Name (year) [tmdbid-XXXX]»), when given, is
// added to the end of the path after a slash.
const props = withDefaults(defineProps<{ catalogName?: string }>(), { catalogName: "" })
const emit = defineEmits<{ (e: "select", value: string): void }>()

interface SavedPath { id: number; path: string }
const paths = ref<SavedPath[]>([])

async function loadPaths(): Promise<void> {
  try {
    const { data } = await api.GET("/api/settings/saved_paths")
    const wrapped = data as unknown as { paths?: SavedPath[] } | null
    if (wrapped && Array.isArray(wrapped.paths)) paths.value = wrapped.paths
  } catch {
    // A list that cannot be had is empty: typing the path by hand stays open.
  }
}

const items = computed(() => paths.value.map((each) => ({ value: each.path, label: each.path })))

function choose(base: string): void {
  const name = (props.catalogName || "").trim()
  emit("select", name ? base.replace(/\/+$/, "") + "/" + name : base)
}
</script>

<template>
  <PickDropdown :items="items" :title="t('savedPaths.title')" :empty="t('savedPaths.empty')" :load="loadPaths" @select="choose" />
</template>
