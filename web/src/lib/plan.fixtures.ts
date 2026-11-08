import type { components } from "../api/schema"
import type { PlannerView } from "./plan"

// Samples of the plan of a series in the shape of the schema: a goal per episode, an asset per file, the failures.
type Schemas = components["schemas"]
type Ref = Schemas["PlanRowRef"]

export const rowRef = (sourceId: number, rowKey: string, video = ""): Ref => ({ source_id: sourceId, row_key: rowKey, video })

export const planView = (over: Partial<PlannerView> = {}): PlannerView => ({
  series_id: 1, running: false, assets: [], episodes: [], failures: [], commands: [], ...over,
})

export const goalVideo = (kind: "asset" | "download" | "cut", row: Ref, file: string | null, cutOf: Ref | null = null): Schemas["PlanGoalVideo"] => ({
  source: "", player: "vibix", group: null, height: 1080, link: "", label: "", kind, row, file, cut_of: cutOf,
})

export const goalTrack = (voice: string, kind: "reuse" | "download" | "realign", row: Ref,
                          over: Partial<Schemas["PlanGoalTrack"]> = {}): Schemas["PlanGoalTrack"] => ({
  source: "", player: "vibix", group: voice, height: 1080, link: "", label: voice, voice, kind, row, own: false, ...over,
})

export const goal = (slot: number[], video: Schemas["PlanGoalVideo"],
                     tracks: Schemas["PlanGoalTrack"][] = []): Schemas["PlanEpisode"] => ({
  slot, name: slot.join(":"), off: false, video, tracks,
})

export const assetTrack = (voice: string, row: Ref, online: boolean | null = true,
                           over: Partial<Schemas["PlanAssetTrack"]> = {}): Schemas["PlanAssetTrack"] => ({
  source: "", player: "vibix", group: voice, height: 1080, link: "", label: voice, row, online, ...over,
})

export const asset = (name: string, row: Ref, tracks: Schemas["PlanAssetTrack"][] = [], cutOf: Ref | null = null,
                      episode: number[] = [1]): Schemas["PlanAsset"] => ({
  name, off: false, episode,
  video: { source: "", player: "vibix", group: null, height: 1080, link: "", label: "", row, cut_of: cutOf, online: true },
  tracks,
})
