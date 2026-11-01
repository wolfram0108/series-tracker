import { joinVkAddress, sourceTypeOf, splitVkAddress, type SourceType } from "../lib/sources"
import { resolveAddress } from "./sourceCalls"

/** What a typed address is: a page of a site (with the address the source is kept by), an address no site reads, or a refusal in words. */
export type TypedAddress =
  | { kind: "site"; sourceType: SourceType; url: string }
  | { kind: "other" }
  | { kind: "refused"; text: string }

/** The one way a typed address becomes a source: the core says which site reads it and in what form it is kept. */
export async function typeAddress(raw: string): Promise<TypedAddress> {
  const address = raw.trim()
  const answer = await resolveAddress(address)
  if (!answer.ok) return { kind: "refused", text: answer.text }
  const sourceType = sourceTypeOf(answer.data)
  if (sourceType === null) return { kind: "other" }
  const vk = sourceType === "vk_video" ? splitVkAddress(address) : null
  return { kind: "site", sourceType, url: vk ? joinVkAddress(vk.channel, vk.query) : (answer.data.canonical_url ?? address) }
}
