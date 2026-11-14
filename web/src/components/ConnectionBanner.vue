<script setup lang="ts">
import { watch } from "vue"
import { useToast } from "primevue/usetoast"
import { useSSE } from "../composables/useSSE"
import { useAuthStore } from "../stores/auth"
import { t } from "../i18n"

// The window tells a stream that broke: what is on the screen may be old until it is back (the stream reads the data again by itself
// when it returns). The banner comes into sight after a moment (its animation in styles/layout.css), so a break the browser mends
// at once is never seen; a return after one that was seen is told once. Over a login or a setup the banner is not shown: they come first.
const SEEN_AFTER_MS = 1000 // the delay of the animation
const { lost } = useSSE()
const auth = useAuthStore()
const toast = useToast()

let since = 0
watch(lost, (now) => {
  if (now) {
    since = performance.now()
  } else if (since && performance.now() - since > SEEN_AFTER_MS && !auth.showLogin && !auth.needsSetup) {
    toast.add({ severity: "success", summary: t("connection.restored"), life: 3000 })
  }
  if (!now) since = 0
})
</script>

<template>
  <div v-if="lost && !auth.showLogin && !auth.needsSetup" class="connection-banner" role="status">
    <i class="pi pi-exclamation-triangle" aria-hidden="true" /> {{ t("connection.lost") }}
  </div>
</template>
