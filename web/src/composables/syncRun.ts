import type { InjectionKey, Ref } from "vue"

/** What a sync covers when it is not the whole series: a season, or an episode of it. */
export interface SyncScope { season: number; episode?: number }

/** The pass over the series the section «Выход» offers to every place of it that has a button of the sync (components/SyncButton.vue):
 *  the series is synced one pass at a time, so while one runs the button of the sync of any place is the button that stops it. */
export interface SyncRun {
  running: Ref<boolean>
  allowed: Ref<boolean>
  stopping: Ref<boolean>
  sync: (scope?: SyncScope) => Promise<string | null>
  stop: () => Promise<string | null>
}

export const SYNC_RUN: InjectionKey<SyncRun> = Symbol("sync-run")
