import { t, tn, type MessageKey } from "../i18n"
import { reasonText } from "./reason"
import { dubRows, formatDuration, isActive, resultMetrics, splitTitle, verdictOf, type Verdict } from "./conform"
import type { SourceMark } from "./output"
import { phaseText, placeOf } from "./sources"
import {
  CONFORM_DUB_STATES, CONFORM_JOB_STATES, DOWNLOAD_STAGES, DOWNLOAD_STATES, PACK_STAGES, PACK_STATES, QBIT_STATES,
  TORRENT_STAGES, capital, stateLabel, stateOf, stateWord, type StateName,
} from "./states"
import type { ConformTask, DownloadCard, PackCard, PackJournal, QueueTask } from "../stores/queues"
import type { SeriesRead } from "../stores/seriesReads"

/**
 * One card of the work of an agent (the section «Агенты»): every agent — a download, an alignment, a packing,
 * the processing of a torrent, its monitor, the read of a page — is told to the window in this one form, and one component
 * draws it (components/settings/WorkCard.vue). The parts are the same in every card: the series it belongs to, the state
 * with its word and colour (lib/states.ts), the stages, the facts, the progress, the rows of what the work holds, the
 * log of attempts, the foot of the times. A card has the series it is of: the section filters by it.
 */
export type WorkKind = "download" | "conform" | "pack" | "journal" | "processing" | "monitor" | "read"
export type StepState = "done" | "active" | "pending" | "failed" | "skipped"
export type CellKind = "kept" | "new" | "own" | "gone" | "done" | "active" | "pending" | "failed"

export interface WorkFact {
  key: string
  value: string
  /** The mark of the source the value names (the figure and the colour of the source in its series). */
  mark?: SourceMark
  bad?: boolean
}
export interface WorkRow {
  name: string
  metrics: string
  state: StateName
  word: string
  plot?: { src: string; title: string }
}
export interface WorkLine {
  /** A line of the log that is a word of its own (the plan rebuilt, no variant left). */
  title?: string
  mark?: SourceMark
  variant?: string
  attempt?: string
  outcome?: string
  ok?: boolean
  time?: string
}
export type WorkAction = "remove" | "stop" | "cancel" | "delete" | "stopRead"

export interface WorkView {
  id: string
  /** What the core knows the work by (the id of a card, of a job, of a torrent, the address of a page): what an action names. */
  ref: string
  kind: WorkKind
  /** The number of the series the work is for; null for a work that does not tell it (the section then shows it unfiltered). */
  seriesId: number | null
  seriesName: string
  episode: string
  voiceNumber?: number | null
  what: string
  state: StateName
  /** The word of the state when it is not the word of the vocabulary (the verdict of the service, a state a module named). */
  word?: string
  action?: { name: WorkAction; label: string; danger: boolean }
  steps: { label: string; state: StepState; time?: string }[]
  cells: { n: number | string; kind: CellKind; title?: string }[]
  facts: WorkFact[]
  bar?: { percent: number; label?: string }
  rows: WorkRow[]
  log: WorkLine[]
  note?: string
  foot: WorkFact[]
}

// --- how times and sizes are written ---------------------------------------------------------------------------------

export const duration = (seconds: number | null | undefined): string => {
  const whole = Math.max(0, Math.round(seconds ?? 0))
  const h = Math.floor(whole / 3600), m = Math.floor((whole % 3600) / 60), s = whole % 60
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`
}
export const clock = (iso: string | null | undefined, seconds = true): string => iso
  ? new Date(iso).toLocaleTimeString("ru-RU", seconds ? undefined : { hour: "2-digit", minute: "2-digit" }) : ""
const mb = (value: number): string => t("download.value.mb", { value })
const fact = (key: string, value: string | number | null | undefined, extra: Partial<WorkFact> = {}): WorkFact[] =>
  value === null || value === undefined || value === "" ? [] : [{ key, value: String(value), ...extra }]
const percentOf = (done: number, all: number): number => (all > 0 ? Math.min(100, Math.round((done * 100) / all)) : 0)
const pad = (n: number): string => String(n).padStart(2, "0")
/** `s01e30`, or `s01e09–e12` for a compilation: how an episode is written everywhere in the window. */
export function slotLabel(season: number | null | undefined, start: number | null | undefined, end?: number | null): string {
  if (start == null) return ""
  const season_ = season == null ? "" : `s${pad(season)}`
  return `${season_}e${pad(start)}${end != null && end > start ? `–e${pad(end)}` : ""}`
}

const steps = (list: readonly { name: string; state: string; seconds?: number | null }[], word: (name: string) => string) =>
  list.map((stage) => ({
    label: word(stage.name), state: stage.state as StepState,
    ...((stage.seconds ?? 0) >= 0.5 ? { time: duration(stage.seconds) } : {}),
  }))

type DownloadVariant = NonNullable<DownloadCard["variant"]>
const markOf = (variant: DownloadVariant): SourceMark => ({ n: variant.source_n || 0, color: variant.source_color ?? 0, place: variant.place ?? "" })
const variantText = (variant: DownloadVariant): string => [variant.place, variant.player_label || variant.player,
  variant.height ? t("download.value.cap", { height: variant.height }) : ""].filter(Boolean).join(" · ")

// --- a download ---------------------------------------------------------------------------------------------------

export function downloadWork(card: DownloadCard): WorkView {
  const figures = card.figures ?? {}
  const log = card.log ?? []
  const stages = card.stages ?? []
  const active = stages.find((stage) => stage.state === "active")?.name ?? null
  const ended = card.status === "done" || card.status === "failed"
  const withBar = (active === "download" || active === "remux") && figures.progress != null
  const exhausted = log.at(-1)?.kind === "exhausted"
  const lineText = (line: NonNullable<DownloadCard["log"]>[number]): string => line.outcome === "done" ? stateWord("done")
    : line.outcome === "failed" ? t(`download.failed.${line.stage ?? "link"}` as MessageKey)
      : stateWord(DOWNLOAD_STAGES[active ?? "queue"])
  return {
    id: `download:${card.id}`, ref: card.id, kind: "download", seriesId: card.need.series_id, seriesName: card.need.series_name ?? "",
    episode: card.need.episode, voiceNumber: card.need.kind === "dub" ? card.need.voice_number : null,
    what: card.need.kind === "dub" ? card.need.group ?? "" : t(`download.kind.${card.need.kind}` as MessageKey),
    state: DOWNLOAD_STATES[card.status],
    ...(ended ? { action: { name: "remove" as const, label: t("download.remove"), danger: true } } : {}),
    steps: steps(stages, (name) => stateLabel(DOWNLOAD_STAGES[name as keyof typeof DOWNLOAD_STAGES])),
    cells: [],
    facts: [
      ...(card.variant ? fact(t(card.status === "done" ? "download.label.got" : "download.label.variant"), variantText(card.variant),
        { mark: markOf(card.variant) }) : []),
      ...fact(t("download.label.cap"), card.need.cap ? t("download.value.cap", { height: card.need.cap }) : t("download.value.noCap")),
      ...(card.method ? fact(t("download.label.method"), t(`download.method.${card.method}` as MessageKey)) : []),
      ...(card.status === "queued" && card.position ? [
        ...fact(t("download.label.position"), card.position),
        ...fact(t("download.label.running"), t("download.value.of", { done: card.running, all: card.limit }))] : []),
      ...(withBar ? [
        ...(figures.total_mb && active === "download" ? fact(t("download.label.loaded"),
          t("download.value.of", { done: mb(figures.done_mb ?? 0), all: mb(figures.total_mb) })) : []),
        ...(figures.speed && active === "download" ? fact(t("download.label.speed"), t("download.value.mbs", { value: figures.speed })) : []),
        ...(figures.speed_x ? fact(t("download.label.speed"), t("download.value.x", { value: figures.speed_x })) : []),
        ...(figures.eta != null ? fact(t("download.label.left"), duration(figures.eta)) : []),
        ...(figures.source_seconds ? fact(t("download.label.sourceLength"), duration(figures.source_seconds)) : [])]
        : card.status === "done" ? [
          ...(figures.got_height ? fact(t("download.label.quality"), t("download.value.cap", { height: figures.got_height })) : []),
          ...(figures.got_seconds ? fact(t("download.label.length"), duration(figures.got_seconds)) : []),
          ...(figures.size_mb ? fact(t("download.label.size"), mb(figures.size_mb)) : [])] : []),
      ...(card.status === "failed" && exhausted ? [
        ...fact(t("download.label.outcome"), t(`download.outcome.${card.need.kind}` as MessageKey)),
        ...fact(t("download.label.repeat"), t("download.outcome.repeat"))] : []),
    ],
    ...(withBar ? { bar: { percent: figures.progress ?? 0 } } : {}),
    rows: [],
    log: log.flatMap((line): WorkLine[] => {
      if (line.kind === "attempt" && line.variant) {
        return [{ mark: markOf(line.variant), variant: variantText(line.variant), attempt: t("download.attempt", { n: line.attempt, of: line.of }),
          outcome: lineText(line), ok: line.outcome === "done" ? true : line.outcome === "failed" ? false : undefined, time: duration(line.seconds) }]
      }
      if (line.kind === "replan" && line.variant) {
        return [{ title: t("download.replan"), mark: markOf(line.variant), variant: variantText(line.variant) }]
      }
      return line.kind === "exhausted" ? [{ title: t("download.exhausted") }] : []
    }),
    foot: [
      ...fact(t("download.label.queuedAt"), clock(card.queued_at)),
      ...(card.finished_at ? fact(t(card.status === "failed" ? "download.label.failedAt" : "download.label.finishedAt"), clock(card.finished_at)) : []),
      ...fact(t("download.label.total"), duration(card.seconds)),
    ],
  }
}

// --- a container ----------------------------------------------------------------------------------------------------

export function packWork(card: PackCard): WorkView {
  const figures = card.figures ?? {}
  const tracks = card.tracks ?? []
  const placed = tracks.filter((track) => track.kind !== "gone")
  const added = tracks.filter((track) => track.kind === "new")
  const gone = tracks.filter((track) => track.kind === "gone")
  const title = (track: NonNullable<PackCard["tracks"]>[number]): string => t(`pack.track.${track.kind}` as MessageKey, { name: track.label })
  return {
    id: `pack:${card.id}`, ref: card.id, kind: "pack", seriesId: card.series_id, seriesName: card.series_name ?? "", episode: card.episode,
    what: t("work.kind.container"), state: PACK_STATES[card.status],
    ...(card.status === "failed" ? { action: { name: "remove" as const, label: t("download.remove"), danger: true } } : {}),
    steps: steps(card.stages ?? [], (name) => stateLabel(PACK_STAGES[name as keyof typeof PACK_STAGES])),
    cells: [...placed, ...gone].map((track) => ({ n: track.number ?? "?", kind: (track.kind === "new" ? "new" : track.kind) as CellKind, title: title(track) })),
    facts: [
      ...fact(t("pack.label.file"), card.name),
      ...(card.video ? fact(t("pack.label.video"), variantText(card.video), { mark: markOf(card.video) }) : []),
      ...fact(t("download.label.sync"), `#${card.series_id} · ${clock(card.sync_started, false)}`),
      ...(card.reason ? fact(t("pack.label.reason"), t(`pack.reason.${card.reason}` as MessageKey)) : []),
      ...(card.status === "build" && figures.progress != null ? [
        ...(figures.written_mb ? fact(t("pack.label.written"), mb(figures.written_mb)) : []),
        ...(figures.speed_x ? fact(t("download.label.speed"), t("download.value.x", { value: figures.speed_x })) : []),
        ...(figures.eta != null ? fact(t("download.label.left"), duration(figures.eta)) : [])] : []),
      ...(card.status === "built" ? fact(t("pack.label.waits"), t("pack.value.waits", { time: duration(figures.wait_s) })) : []),
      ...(card.status === "apply" && figures.steps_of ? fact(t("pack.label.steps"),
        t("download.value.of", { done: figures.steps_done ?? 0, all: figures.steps_of })) : []),
      ...(card.status === "done" && figures.size_mb ? [
        ...fact(t("download.label.size"), mb(figures.size_mb)),
        ...fact(t("pack.label.tracks"), placed.length),
        ...((figures.wait_s ?? 0) >= 0.5 ? fact(t("pack.label.waited"), duration(figures.wait_s)) : [])] : []),
      ...added.map((track) => ({ key: `+ ${track.number ?? "?"}`, value: track.label })),
      ...gone.map((track) => ({ key: `− ${track.number ?? "?"}`, value: track.label, bad: true })),
      ...(card.status === "failed" ? [
        ...fact(t("download.label.outcome"), t(card.waited ? "pack.outcome.waited" : "pack.outcome.failed")),
        ...(card.failure ? fact(t("pack.label.reason"), t(`pack.failure.${card.failure}` as MessageKey)) : []),
        ...fact(t("download.label.repeat"), t("download.outcome.repeat"))] : []),
    ],
    ...(card.status === "build" && figures.progress != null ? { bar: { percent: figures.progress } } : {}),
    rows: [], log: [],
    foot: [
      ...fact(t("download.label.queuedAt"), clock(card.queued_at)),
      ...(card.finished_at ? fact(t(card.status === "failed" ? "download.label.failedAt" : "download.label.finishedAt"), clock(card.finished_at)) : []),
      ...fact(t("download.label.total"), duration(card.seconds)),
    ],
  }
}

/** The journal of a series being carried out: the files a sync puts in place, one step after another. */
export function journalWork(journal: PackJournal): WorkView {
  const done = journal.steps_done ?? 0, of = journal.steps_of ?? 0
  return {
    id: `journal:${journal.series_id}`, ref: String(journal.series_id), kind: "journal", seriesId: journal.series_id, seriesName: journal.series_name ?? "",
    episode: t(`pack.journal.kind.${journal.kind ?? "sync"}` as MessageKey), what: "", state: "apply",
    steps: [], cells: [],
    facts: [
      ...fact(t("pack.journal.stepsKey"), t("download.value.of", { done, all: of })),
      ...(journal.step ? fact(t(`pack.journal.step.${journal.step}` as MessageKey), journal.file) : []),
    ],
    bar: { percent: percentOf(done, of) }, rows: [], log: [],
    foot: [
      ...(journal.started_at ? fact(t("pack.journal.since"), clock(journal.started_at)) : []),
      ...fact(t("pack.journal.time"), duration(journal.seconds)),
    ],
  }
}

// --- an alignment -----------------------------------------------------------------------------------------------------

/** The colour a verdict of the service is drawn in: it is told by a word of its own, not by a state of the vocabulary. */
const VERDICT_TONE: Record<Verdict, StateName> = { accepted: "done", unsure: "absent", rejected: "failed", failed: "failed" }
const VERDICT_WORD: Record<Verdict, MessageKey> = {
  accepted: "agents.conform.verdict.accepted", unsure: "agents.conform.verdict.unsure",
  rejected: "agents.conform.verdict.rejected", failed: "agents.conform.verdict.failed",
}

export function conformWork(task: ConformTask, nameOf: (file: string) => string): WorkView {
  const { episode } = splitTitle(task.label)
  const rows = dubRows(task, nameOf).map((row): WorkRow => {
    if (row.result) {
      const verdict = verdictOf(row.result)
      return {
        name: row.name, metrics: resultMetrics(row.result), state: VERDICT_TONE[verdict], word: capital(t(VERDICT_WORD[verdict])),
        ...(row.result.plot ? { plot: { src: `/api/conform/jobs/${encodeURIComponent(task.id)}/plot/${encodeURIComponent(row.result.plot)}`,
          title: t("agents.conform.plot.title", { dub: row.name, episode: episode.toLowerCase() }) } } : {}),
      }
    }
    const state = CONFORM_DUB_STATES[row.state]
    return {
      name: row.name, metrics: "", state,
      word: row.state === "running" ? `${stateLabel(state)} ${t("agents.conform.dub.position", { index: task.dub_index, total: task.dub_total })}` : stateLabel(state),
    }
  })
  const active = isActive(task.status)
  return {
    id: `conform:${task.id}`, ref: task.id, kind: "conform", seriesId: task.series_id ?? null, seriesName: "", episode: episode.toLowerCase(),
    what: task.dub_total ? tn("work.dubs", task.dub_total) : "", state: stateOf(CONFORM_JOB_STATES, task.status) ?? "planned",
    word: stateOf(CONFORM_JOB_STATES, task.status) ? undefined : task.status,
    action: !active ? { name: "delete", label: t("agents.conform.action.delete"), danger: true }
      : task.status === "running" ? { name: "stop", label: t("agents.conform.action.stop"), danger: false }
        : { name: "cancel", label: t("agents.conform.action.cancel"), danger: false },
    steps: [], cells: [], facts: [],
    ...(task.status === "running" ? { bar: { percent: Math.round(task.progress * 100), label: task.stage } } : {}),
    rows, log: [],
    foot: [
      ...(active && task.status !== "running" && task.created_at
        ? fact(t("agents.conform.addedAt"), clock(task.created_at, false))
        : fact(t("agents.conform.elapsedLabel"), formatDuration(task.elapsed_s || 0))),
      ...fact(t("work.job"), task.id.slice(0, 8)),
    ],
  }
}

// --- a torrent ---------------------------------------------------------------------------------------------------------

const str = (value: unknown): string => String(value ?? "")
const STAGE_STEP: Record<string, number> = {
  awaiting_metadata: 0, polling_for_size: 0, awaiting_pause_before_rename: 0, renaming: 1, rechecking: 2, activating: 3,
}
const STAGE_WORDS: readonly StateName[] = ["metadata", "renaming", "checking", "activating"]

/** A torrent in the pipeline of the torrents module: the stages it goes through, the one it stands at. */
export function processingWork(task: QueueTask): WorkView {
  const stage = str(task.stage)
  const at = STAGE_STEP[stage]
  return {
    id: `processing:${str(task.hash)}`, ref: str(task.hash), kind: "processing", seriesId: Number(task.series_id) || null, seriesName: "", episode: "",
    what: t("work.torrent", { id: str(task.torrent_id) }), state: stateOf(TORRENT_STAGES, stage) ?? "planned",
    // The stage that failed is not told: a torrent in error shows its stages as they were not reached.
    steps: STAGE_WORDS.map((name, index) => ({ label: stateLabel(name), state: at === undefined ? "pending" : index < at ? "done" : index === at ? "active" : "pending" })),
    cells: [], facts: fact(t("work.label.torrent"), `${str(task.hash).slice(0, 8)}…`), rows: [], log: [], foot: [],
  }
}

const MIB = 1024 * 1024
/** A torrent in qBittorrent: how far it is, how fast, how long it takes still. */
export function monitorWork(task: QueueTask): WorkView {
  const status = str(task.status)
  const progress = Number(task.progress) || 0
  const state = stateOf(QBIT_STATES, status) ?? "planned"
  const speed = Number(task.dlspeed) || 0
  const eta = Number(task.eta) || 0
  return {
    id: `monitor:${str(task.qb_hash)}`, ref: str(task.qb_hash), kind: "monitor", seriesId: Number(task.series_id) || null, seriesName: str(task.series_name),
    episode: "", what: `${str(task.qb_hash).slice(0, 8)}…`, state, word: stateOf(QBIT_STATES, status) ? undefined : status,
    steps: [], cells: [],
    facts: [
      ...(speed > 0 ? fact(t("download.label.speed"), t("download.value.mbs", { value: (speed / MIB).toFixed(1).replace(".", ",") })) : []),
      ...(state === "download" && eta > 0 && eta < 8640000 ? fact(t("download.label.left"), duration(eta)) : []),
    ],
    ...(progress > 0 || state === "download" ? { bar: { percent: progress } } : {}),
    rows: [], log: [], ...(task.error_message ? { note: reasonText(str(task.error_message)) } : {}), foot: [],
  }
}
// --- the read of a page ------------------------------------------------------------------------------------------------

export function readWork(read: SeriesRead & { seriesId: number }): WorkView {
  return {
    id: `read:${read.seriesId}:${read.sourceId}`, ref: read.url, kind: "read", seriesId: read.seriesId, seriesName: "", episode: "",
    what: placeOf(read.url), state: "read",
    action: { name: "stopRead", label: t("sources.stopRead"), danger: false },
    steps: [], cells: [],
    facts: [
      ...fact(phaseText(read.phase), read.total > 0 ? t("download.value.of", { done: read.current, all: read.total }) : ""),
      ...(read.rate != null ? fact(t("download.label.speed"), t("work.rate", { rate: read.rate.toFixed(1) })) : []),
    ],
    ...(read.total > 0 ? { bar: { percent: percentOf(read.current, read.total) } } : {}),
    rows: [], log: [], foot: [],
  }
}
