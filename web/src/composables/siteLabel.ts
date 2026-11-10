import { ref, type Ref } from "vue"
import { resolveAddress } from "./sourceCalls"

// The core names the site of an address without the network; one ask per address serves every window that shows it.
const labels = new Map<string, Ref<string>>()

/** The name of the site that reads an address, as the core gives it (a video application by its own name, `半日`);
 *  empty until the core answers, and when no site reads the address. */
export function siteLabel(url: string): Ref<string> {
  const found = labels.get(url)
  if (found) return found
  const made = ref("")
  labels.set(url, made)
  void resolveAddress(url).then((answer) => {
    if (answer.ok) made.value = answer.data.label ?? ""
  })
  return made
}
