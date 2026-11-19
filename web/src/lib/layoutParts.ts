import type { components } from "../api/schema"

type Schemas = components["schemas"]
type Composition = Schemas["CompositionReply"]

/** What of the layout a change touched, and so what a window that holds the layout reads again (the core's `LayoutPart`). */
export type LayoutPart = Schemas["CompositionChanged"]["part"]

/** The news of the layout of a series since the window last read it: the widest part, and the episodes of the part
 *  `levers` (null: every episode). */
export interface LayoutNews {
  part: LayoutPart
  episodes: Set<string> | null
}

const WIDTH: Record<LayoutPart, number> = { sources: 0, levers: 1, whole: 2 }

export const episodeKey = (season: number, episode: number): string => `${season}:${episode}`

/** Two news as one: the wider part, and every episode either names. */
export function joined(held: LayoutNews | undefined, more: LayoutNews): LayoutNews {
  if (!held) return more
  return {
    part: WIDTH[more.part] > WIDTH[held.part] ? more.part : held.part,
    episodes: held.episodes === null || more.episodes === null ? null : new Set([...held.episodes, ...more.episodes]),
  }
}

/** The news a packet adds to the news held. */
export function noted(held: LayoutNews | undefined, packet: Schemas["CompositionChanged"]): LayoutNews {
  const part = packet.part ?? "whole"
  const listed = packet.episodes ?? []
  const episodes = part !== "levers" ? new Set<string>()
    : listed.length ? new Set(listed.map((each) => episodeKey(each.season, each.episode))) : null
  return joined(held, { part, episodes })
}

/** The layout held with a part of it the core gave: what the part holds replaces what is held, the rest stands. */
export function merged(held: Composition, reply: Composition, episodes: Set<string> | null): Composition {
  if (reply.part === "sources") return { ...held, rev: reply.rev, sources: reply.sources }
  if (reply.part !== "levers") return reply
  const settings = episodes === null ? reply.settings : [
    ...(held.settings ?? []).filter((each) => !episodes.has(episodeKey(each.season, each.episode))),
    ...(reply.settings ?? []),
  ].sort((one, two) => one.season - two.season || one.episode - two.episode)
  return {
    ...held, rev: reply.rev, settings, levels: reply.levels, compilations: reply.compilations,
    voice_orders: reply.voice_orders, voice_titles: reply.voice_titles, sources: reply.sources,
  }
}
