import type { Composition, Queues } from "../composables/outputCalls"
import type { Reference, Row } from "../lib/output"
import type { PlannerView } from "../lib/plan"
import { AVAILABILITY, SPECS } from "../lib/output.fixtures"
import { asset, assetTrack, goal, goalTrack, goalVideo, planView, rowRef } from "../lib/plan.fixtures"

// The sample series of tools/OutputSim.vue: one season of `count` episodes, each with two voice-overs from two sources, its
// reference, its goal in the plan and, for two of three episodes, a file already held.

export const SIM_SERIES = 1

const row = (episode: number, over: Partial<Row> = {}): Row => ({
  series_id: SIM_SERIES, source_id: 7, in_output: true, row_key: `r1-${episode}`, excluded: false, season: 1, episode_start: episode,
  episode_end: episode, voice_label: "Ruchime", voice_name: "Ruchime", player: "vibix", player_label: "Vibix",
  group: "", text: "", heights: [1080], state: "playable", link: "", video: "", timeline: "", track: "sound", language: "", subtitle_type: "",
  stale: false, ...over,
})

const reference = (episode: number): Reference => ({
  season: 1, episode, part: null, mark: "by_level", source_id: 7, row_key: `r1-${episode}`, player: "vibix", compilation: null,
  voice_group: "Ruchime", height: 1080, reason: null, picture: null, markup: false,
})

const episodes = (count: number): number[] => Array.from({ length: count }, (_, at) => at + 1)

export const simComposition = (count: number): Composition => ({
  series_id: SIM_SERIES, rev: 1, part: "whole", sources: [], settings: [],
  rows: episodes(count).flatMap((episode) => [row(episode),
    row(episode, { row_key: `k1-${episode}`, source_id: 8, player: "kodik", player_label: "Kodik", voice_label: "AniStar", voice_name: "AniStar" })]),
  levels: [{ season: null, levers: [] }, { season: 1, levers: [] }],
  levers: SPECS, availability: AVAILABILITY,
  voice_orders: [{ season: 1, voices: [{ label: "Ruchime", episodes: `1–${count}` }, { label: "AniStar", episodes: `1–${count}` }] }],
})

/** The plan as the core pushes it: the episodes switched off are `off`. */
export function simPlan(count: number, off: ReadonlySet<number>): PlannerView {
  const file = (episode: number) => `S01E${String(episode).padStart(4, "0")}.mkv`
  const held = (episode: number) => episode % 3 !== 0
  return {
    ...planView({
      series_id: SIM_SERIES,
      episodes: episodes(count).map((episode) => ({
        ...goal([1, episode], goalVideo(held(episode) ? "asset" : "download", rowRef(7, `r1-${episode}`), held(episode) ? file(episode) : null),
          [goalTrack("Ruchime", held(episode) ? "reuse" : "download", rowRef(7, `r1-${episode}`)),
            goalTrack("AniStar", "download", rowRef(8, `k1-${episode}`))]),
        off: off.has(episode),
      })),
      assets: episodes(count).filter(held).map((episode) =>
        asset(file(episode), rowRef(7, `r1-${episode}`), [assetTrack("Ruchime", rowRef(7, `r1-${episode}`))], null, [1, episode])),
    }),
    references: episodes(count).map(reference),
  }
}

/** The queues of an opened episode (its reference and its voice-overs). */
export const simQueues = (episode: number): Queues => ({
  series_id: SIM_SERIES, season: 1, episode, markup_follows: true,
  reference: { order_level: "default", same_level: "default", rows: [
    { group: "Ruchime", source_id: 7, player: "vibix", player_label: "Vibix", height: 1080, episodes: String(episode), stale: "", allowed: true,
      allowed_level: "default", language: "", subtitle_type: "", default: false, forced: false },
    { group: "AniStar", source_id: 8, player: "kodik", player_label: "Kodik", height: 720, episodes: String(episode), stale: "", allowed: true,
      allowed_level: "default", language: "", subtitle_type: "", default: false, forced: false },
  ] },
  groups: [{ group: "AniStar", take: true, take_level: "default", order_level: "default", same_level: "default", rows: [
    { group: "AniStar", source_id: 8, player: "kodik", player_label: "Kodik", height: 720, episodes: String(episode), stale: "", allowed: true,
      allowed_level: "default", language: "", subtitle_type: "", default: false, forced: false },
  ] }],
})
