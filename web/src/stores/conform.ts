import { defineStore } from "pinia"
import { ref } from "vue"
import type { components } from "../api/schema"

/** Packet of conform_settings_update: the settings of the alignment server as they apply after a save, whole. */
export type ConformSettingsView = components["schemas"]["ConformSettingsView"]

export const useConformStore = defineStore("conform", () => {
  // The newest settings the core announced; the settings screen follows it.
  const settings = ref<ConformSettingsView | null>(null)

  function applySettings(next: ConformSettingsView): void {
    settings.value = next
  }

  return { settings, applySettings }
})
