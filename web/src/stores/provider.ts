import { defineStore } from "pinia"
import { ref } from "vue"
import type { components } from "../api/schema"

/** Packet of provider_update: the whole picture of the provider of external sources (its address, the state of the socket, what its
 *  manifest says, its applications). */
export type ProviderView = components["schemas"]["ProviderView"]

export const useProviderStore = defineStore("provider", () => {
  // The newest picture the core told; the tab of the provider follows it.
  const view = ref<ProviderView | null>(null)

  function apply(next: ProviderView): void {
    // The announcement of an earlier change may come after the answer to a later one: an older picture is not taken.
    if (view.value && next.seq < view.value.seq) return
    view.value = next
  }

  return { view, apply }
})
