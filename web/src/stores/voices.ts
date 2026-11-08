import { defineStore } from "pinia"
import { ref } from "vue"
import type { components } from "../api/schema"

type Schemas = components["schemas"]

/** Packet of voice_settings_update: the settings as they apply after a save, whole, so nothing is asked back. */
export type VoiceSettingsView = Schemas["VoiceSettingsView"]

/** The settings of the voice-overs as the core announced them last; the settings screen follows it. The voice-overs of
 *  the episodes are the plan of each series (stores/plan.ts). */
export const useVoicesStore = defineStore("voices", () => {
  const settings = ref<VoiceSettingsView | null>(null)

  function applySettings(next: VoiceSettingsView): void {
    settings.value = next
  }

  /** The name of a file of a job of the alignment service, as its queue shows it: the file without its extension. */
  function dubName(_jobId: string, file: string): string {
    return file.replace(/^.*\//, "").replace(/\.[^.]+$/, "")
  }

  return { settings, applySettings, dubName }
})
