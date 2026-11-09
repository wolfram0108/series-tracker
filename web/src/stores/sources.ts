import { defineStore } from "pinia"
import { ref } from "vue"
import type { components } from "../api/schema"

/** Packet of sources_settings_update: the settings screen of the sources whole — the mirrors of each tracker and the
 *  domains read now — so nothing is asked back. */
export type SourcesSettingsView = components["schemas"]["SourcesSettingsView"]

/** The settings of the sources as the core announced them last; the tab «Источники» follows it. */
export const useSourcesStore = defineStore("sources", () => {
  const settings = ref<SourcesSettingsView | null>(null)

  function applySettings(next: SourcesSettingsView): void {
    settings.value = next
  }

  return { settings, applySettings }
})
