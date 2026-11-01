import { ref } from "vue"
import { api } from "../api/client"
import type { components } from "../api/schema"
import { t } from "../i18n"
import { reasonText } from "../lib/reason"

type View = components["schemas"]["VoiceSettingsView"]

/**
 * Making a group of voice-overs from a window that shows a voice-over without one. The settings are read right before
 * the save, so that a group made elsewhere a moment ago is kept; the group takes the names it is made from as its
 * triggers, and a name the core refuses comes back in words.
 */
export function useVoiceGroups() {
  const busy = ref(false)

  /** `null` when the group was saved, else the words of the refusal. */
  async function makeGroup(name: string, triggers: string[]): Promise<string | null> {
    const wanted = name.trim()
    if (!wanted) return t("voices.group.nameEmpty")
    busy.value = true
    try {
      const current = await api.GET("/api/voices/settings")
      if (!current.data) return t("voices.settings.loadFailed")
      const view: View = current.data
      const kept = view.voice_groups[wanted] ?? []
      const voiceGroups = { ...view.voice_groups, [wanted]: [...new Set([...kept, ...triggers])] }
      const saved = await api.PUT("/api/voices/settings", { body: { priority: view.priority, voice_groups: voiceGroups } })
      if (!saved.data) return t("voices.settings.saveFailed")
      if (saved.data.saved) return null
      return saved.data.problems.map((problem) => reasonText(problem.detail)).join("; ") || t("voices.settings.saveFailed")
    } catch {
      return t("voices.settings.saveFailed")
    } finally {
      busy.value = false
    }
  }

  return { busy, makeGroup }
}
