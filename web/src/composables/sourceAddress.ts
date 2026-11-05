import { t } from "../i18n"
import { joinVkAddress, sourceTypeOf, splitVkAddress, vkQuery, type SourceType } from "../lib/sources"
import { resolveAddress } from "./sourceCalls"

/** What kind of address the core says a typed one is: a page of a site, an address no site reads, or a refusal in words. */
export type AddressKind =
  | { kind: "site"; sourceType: SourceType }
  | { kind: "other" }
  | { kind: "refused"; text: string }

/** What a typed address is: a page of a site (with the address the source is kept by), an address no site reads, or a refusal in words. */
export type TypedAddress =
  | { kind: "site"; sourceType: SourceType; url: string }
  | { kind: "other" }
  | { kind: "refused"; text: string }

/** Which kind of source a typed address would make, as the core says, with no read; a window asks it while the user types. */
export async function kindOf(raw: string): Promise<AddressKind & { canonical?: string }> {
  const answer = await resolveAddress(raw.trim())
  if (!answer.ok) return { kind: "refused", text: answer.text }
  const sourceType = sourceTypeOf(answer.data)
  return sourceType === null ? { kind: "other" } : { kind: "site", sourceType, canonical: answer.data.canonical_url ?? undefined }
}

/** The one way a typed address becomes a source: the core says which site reads it and in what form it is kept. A channel of
 *  VK is read whole and only its query keeps its videos, so a channel without a query (typed in the address or given apart)
 *  is refused: nothing is read for it. */
export async function typeAddress(raw: string, query = ""): Promise<TypedAddress> {
  const address = raw.trim()
  const found = await kindOf(address)
  if (found.kind !== "site") return found
  if (found.sourceType !== "vk_video") return { kind: "site", sourceType: found.sourceType, url: found.canonical ?? address }
  const vk = splitVkAddress(address)
  const terms = vkQuery(query) || vkQuery(vk.query)
  if (!terms) return { kind: "refused", text: t("sources.vk.queryMissing") }
  return { kind: "site", sourceType: "vk_video", url: joinVkAddress(vk.channel, terms) }
}
