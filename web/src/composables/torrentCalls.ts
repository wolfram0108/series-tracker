// The calls of the tab «Торрент» (stage 15, step 9): each is one request of modules/gateway/api_torrents.py; what it
// changes comes back as events (torrent_release_update, torrent_editor_update, torrent_read_update).
import { api } from "../api/client"
import type { components } from "../api/schema"
import type { Editor, EditorTab, PageReleases, ReleaseView } from "../lib/torrent"
import { call, type Answer, type CallWords } from "./sourceCalls"

const WORDS: CallWords = { refused: "torrent.error.refused", network: "torrent.error.network" }

export const fetchPage = (url: string): Promise<Answer<PageReleases>> =>
  call(() => api.POST("/api/torrent/page", { body: { url } }), WORDS)

export const fetchReleases = (url: string): Promise<Answer<{ releases: ReleaseView[] }>> =>
  call(() => api.POST("/api/torrent/releases", { body: { url } }), WORDS)

export const downloadRelease = (url: string, release: string): Promise<Answer<ReleaseView>> =>
  call(() => api.POST("/api/torrent/download", { body: { url, release } }), WORDS)

export const watchRelease = (url: string, release: string, watched: boolean): Promise<Answer<ReleaseView>> =>
  call(() => api.POST("/api/torrent/watch", { body: { url, release, watched } }), WORDS)

export const fetchEditor = (url: string): Promise<Answer<Editor>> =>
  call(() => api.POST("/api/torrent/editor", { body: { url } }), WORDS)

type Schemas = components["schemas"]
/** A body whose fields with a default on the core may be left out. */
type WithDefaults<T, K extends keyof T> = Omit<T, K> & Partial<Pick<T, K>>

/** Items placed by hand: a file in an episode (to `episode_last` for a file of several), a track in a folder, or left out. */
export const assign = (body: WithDefaults<Schemas["AssignBody"], "excluded" | "in_order">): Promise<Answer<unknown>> =>
  call(() => api.POST("/api/torrent/editor/assign", { body: { excluded: false, in_order: false, ...body } }), WORDS)

export const unassign = (url: string, tab: EditorTab, items: string[]): Promise<Answer<unknown>> =>
  call(() => api.POST("/api/torrent/editor/unassign", { body: { url, tab, items } }), WORDS)

export const setFolder = (body: WithDefaults<Schemas["FolderBody"], "language" | "subtitle_type">): Promise<Answer<unknown>> =>
  call(() => api.POST("/api/torrent/editor/folder", { body: { language: "", subtitle_type: "", ...body } }), WORDS)

export const deleteFolder = (url: string, kind: "voice" | "subtitles", name: string): Promise<Answer<unknown>> =>
  call(() => api.POST("/api/torrent/editor/folder/delete", { body: { url, kind, name } }), WORDS)
