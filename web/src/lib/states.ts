import { t, type MessageKey } from "../i18n"
import type { CutState } from "./compilations"
import type { DubState as ConformDubState } from "./conform"
import type { EpisodeStatus, TrackState } from "./output"
import type { SourceState } from "./sources"

/**
 * The vocabulary of states (docs/interface_vocabulary.md): every state the window shows has one word (`state.<name>`
 * of the catalog), one icon and one colour (`--state-<name>` of styles/tokens.css, set on an element by its
 * `data-state`). A module names its states by its own codes; the tables below turn each code into a state of the
 * vocabulary, so that no screen words or colours a state of its own.
 */
export const STATE_ICONS = {
  planned: "pi-clock",
  queued: "pi-hourglass",
  done: "pi-check",
  failed: "pi-exclamation-triangle",
  stopped: "pi-stop-circle",
  off: "pi-power-off",
  paused: "pi-pause-circle",
  built: "pi-inbox",
  partial: "pi-chart-pie",
  absent: "pi-minus-circle",
  noRow: "pi-minus",
  noFile: "pi-file",
  noReference: "pi-ban",
  download: "pi-download",
  link: "pi-link",
  remux: "pi-replay",
  check: "pi-search",
  align: "pi-sliders-h",
  cut: "pi-arrows-h",
  pack: "pi-box",
  build: "pi-wrench",
  verify: "pi-verified",
  apply: "pi-check-square",
  slice: "pi-clone",
  read: "pi-globe",
  sync: "pi-th-large",
  readDone: "pi-table",
  notRead: "pi-question-circle",
  noProfile: "pi-exclamation-circle",
  checking: "pi-sync",
  activating: "pi-bolt",
  metadata: "pi-file",
  renaming: "pi-pencil",
  moving: "pi-arrow-right",
  idle: "pi-pause",
  viewing: "pi-eye",
} as const

export type StateName = keyof typeof STATE_ICONS
export const STATE_NAMES = Object.keys(STATE_ICONS) as StateName[]

/** The word of a state, as it stands inside a phrase. */
export const stateWord = (name: StateName): string => t(`state.${name}` as MessageKey)

/** The word of a state standing on its own (a pill, a heading): the catalog keeps one word, the label raises its first
 *  letter. */
export const stateLabel = (name: StateName): string => capital(stateWord(name))

export const stateIcon = (name: StateName): string => STATE_ICONS[name]

export function capital(text: string): string {
  return text ? text[0].toLocaleUpperCase("ru") + text.slice(1) : text
}

/** The state of a code a module told, or null for a code the vocabulary does not know (the screen shows the code). */
export function stateOf<C extends string>(table: Readonly<Record<C, StateName>>, code: string | null | undefined):
  StateName | null {
  return code != null && Object.hasOwn(table, code) ? table[code as C] : null
}

// --- the codes of the modules ----------------------------------------------------------------------------------------

/** An episode of the output tab (lib/output.ts episodeStatus). */
export const EPISODE_STATES: Readonly<Record<EpisodeStatus, StateName>> = {
  off: "off", error: "failed", down: "download", align: "align", ready: "done", partial: "partial", wait: "planned",
}

/** A track of an episode, the track of the reference among them (lib/output.ts trackCells). */
export const TRACK_STATES: Readonly<Record<TrackState, StateName>> = {
  done: "done", down: "download", align: "align", wait: "planned", error: "failed", absent: "absent", off: "off",
  none: "noRow",
}

/** The cut of an episode out of its compilation (lib/compilations.ts cutState). */
export const CUT_STATES: Readonly<Record<CutState, StateName>> = {
  wait: "planned", down: "download", align: "cut", ready: "done", error: "failed",
}

/** A card of a download (core/contracts/downloads.py CardStatus) and its stages (Stage). */
export const DOWNLOAD_STATES = {
  queued: "queued", link: "link", download: "download", remux: "remux", check: "check", done: "done", failed: "failed",
} as const satisfies Record<string, StateName>
export const DOWNLOAD_STAGES = {
  queue: "queued", link: "link", download: "download", remux: "remux", check: "check",
} as const satisfies Record<string, StateName>

/** A card of the packing (core/contracts/packing.py PackStatus) and its stages (PackStage). */
export const PACK_STATES = {
  queued: "queued", build: "build", verify: "verify", built: "built", apply: "apply", done: "done", failed: "failed",
} as const satisfies Record<string, StateName>
export const PACK_STAGES = {
  queue: "queued", build: "build", verify: "verify", apply: "apply",
} as const satisfies Record<string, StateName>

/** A job of the alignment daemon, and a voice-over in it (lib/conform.ts). */
export const CONFORM_JOB_STATES = {
  queued: "queued", running: "align", done: "done", failed: "failed", cancelled: "stopped", paused: "paused",
} as const satisfies Record<string, StateName>
export const CONFORM_DUB_STATES: Readonly<Record<ConformDubState, StateName>> = {
  result: "done", running: "align", waiting: "queued", cancelled: "stopped",
}

/** A status of a series on its card (modules/catalog/aggregator.py HIERARCHY). */
export const SERIES_STATES = {
  error: "failed", scanning: "sync", checking: "checking", slicing: "slice", aligning: "align", packing: "pack",
  renaming: "renaming", metadata: "metadata", activating: "activating", downloading: "download", idle: "idle",
  queued: "queued", ready: "done", viewing: "viewing", waiting: "planned",
} as const satisfies Record<string, StateName>

/** A source of a series (lib/sources.ts SourceState). */
export const SOURCE_STATES: Readonly<Record<SourceState, StateName>> = {
  reading: "read", failed: "failed", absent: "notRead", waiting: "planned", noProfile: "noProfile", ready: "readDone",
}

/** A torrent in the pipeline of the torrents module (modules/torrents/pipeline.py STAGE_FLAGS). */
export const TORRENT_STAGES = {
  awaiting_metadata: "metadata", polling_for_size: "metadata", awaiting_pause_before_rename: "metadata",
  renaming: "renaming", rechecking: "checking", activating: "activating", error: "failed",
} as const satisfies Record<string, StateName>

/** A torrent as qBittorrent states it (its WebUI API, as is). */
export const QBIT_STATES = {
  uploading: "done", stalledUP: "done", forcedUP: "done", queuedUP: "done", pausedUP: "done", stoppedUP: "done",
  downloading: "download", forcedDL: "download", allocating: "download", metaDL: "metadata", forcedMetaDL: "metadata",
  stalledDL: "idle", queuedDL: "queued", pausedDL: "stopped", stoppedDL: "stopped", checkingUP: "checking",
  checkingDL: "checking", checkingResumeData: "checking", moving: "moving", error: "failed", missingFiles: "failed",
} as const satisfies Record<string, StateName>

/** The state of an object an action was refused in (core/contracts/availability.py): a series, a download task. */
export const AVAILABILITY_STATES = {
  idle: "idle", scanning: "sync", pending: "queued", downloading: "download", processing: "download", completed: "done",
  error: "failed", absent: "absent",
} as const satisfies Record<string, StateName>

/** A task of the slicing of a torrent compilation (modules/slicing: slicing_tasks.status). */
export const SLICING_STATES = {
  pending: "queued", slicing: "slice", completed: "done", error: "failed",
} as const satisfies Record<string, StateName>
