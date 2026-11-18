import { defineStore } from "pinia"
import { ref } from "vue"
import { api } from "../api/client"
import type { components } from "../api/schema"

/** Packet of provider_update: the whole picture of the provider of external sources (its address, the state of the socket, what its
 *  manifest says, its applications). */
export type ProviderView = components["schemas"]["ProviderView"]

export const useProviderStore = defineStore("provider", () => {
  // The newest picture the core told; the tab of the provider follows it.
  const view = ref<ProviderView | null>(null)
  let asked = false

  function apply(next: ProviderView): void {
    // The announcement of an earlier change may come after the answer to a later one: an older picture is not taken.
    if (view.value && next.seq < view.value.seq) return
    view.value = next
  }

  // The picture is told only when something changes: a screen that writes the name of a site asks for it once, and
  // until it comes (or without a provider) the site is written by its own name.
  async function ensure(): Promise<void> {
    if (view.value || asked) return
    asked = true
    try {
      const { data } = await api.GET("/api/provider")
      if (data) apply(data)
    } catch {
      asked = false
    }
  }

  /** The name of a site of the provider as its manifest gives it for people; the site's own name when it is not known. */
  function labelOf(site: string): string {
    return view.value?.sites.find((each) => each.name === site)?.label || site
  }

  return { view, apply, ensure, labelOf }
})
