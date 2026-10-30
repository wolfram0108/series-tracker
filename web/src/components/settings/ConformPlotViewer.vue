<script setup lang="ts">
import { onMounted, onUnmounted, ref } from "vue"
import Button from "primevue/button"
import ModalShell from "../ModalShell.vue"
import { t } from "../../i18n"

// The picture of an alignment plot in a window over the page; the plot is too detailed to read when it is fitted,
// so the window can switch it to its own size.
defineProps<{ src: string; title: string }>()
const emit = defineEmits<{ (e: "close"): void }>()

const full = ref(false)
const missing = ref(false)

// Escape closes the picture only: the settings window under it stays open.
function onKey(e: KeyboardEvent) {
  if (e.key !== "Escape") return
  e.stopImmediatePropagation()
  emit("close")
}
onMounted(() => window.addEventListener("keydown", onKey, true))
onUnmounted(() => window.removeEventListener("keydown", onKey, true))
</script>

<template>
  <Teleport to="body">
    <ModalShell size="full" :title="title" @close="emit('close')">
      <template #header-extra>
        <Button
          size="small"
          severity="secondary"
          :label="full ? t('agents.conform.plot.zoomFit') : t('agents.conform.plot.zoomFull')"
          @click="full = !full"
        />
      </template>
      <p v-if="missing" class="plot-missing">{{ t("agents.conform.plot.missing") }}</p>
      <div v-else class="plot-pane" :class="{ full }">
        <img :src="src" :alt="title" @error="missing = true" />
      </div>
    </ModalShell>
  </Teleport>
</template>

<style scoped>
.plot-pane { overflow: auto; }
.plot-pane img { display: block; max-width: 100%; height: auto; margin: 0 auto; }
.plot-pane.full img { max-width: none; margin: 0; }
.plot-missing { color: var(--text-muted); text-align: center; margin: 24px 0; }
</style>
