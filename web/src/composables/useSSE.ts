import { ref } from "vue"

/** SSE events of the contract (from the gateway SSE_MAP). The stream is read in frames of a turn
 *  (`frames=batch`): `event: batch\ndata: [{event, data}, ...]`, the events in their order; EventSource ignores the
 *  keepalive comments itself. */
export const SSE_EVENTS = [
  "series_updated",
  "series_added",
  "series_deleted",
  "agent_queue_update",
  "torrent_progress_update",
  "download_queue_update",
  "slicing_queue_update",
  "conform_queue_update",
  "pack_queue_update",
  "voice_dub_update",
  "voice_dubs_snapshot",
  "voice_settings_update",
  "conform_settings_update",
  "scanner_status_update",
  "sources_table_progress",
  "series_scan_progress",
  "sources_table_ready",
  "sources_table_failed",
  "composition_changed",
  "composition_failed",
  "reference_changed",
  "series_sources_changed",
  "renaming_complete",
  "relocation_started",
  "relocation_finished",
  "preview_naming_ready",
  "preview_naming_failed",
] as const

export type SSEEvent = (typeof SSE_EVENTS)[number]
type Handler = (data: unknown) => void

// Модуль-синглтон: одно соединение и общий реестр на всё приложение
// (инвариант «одно SSE-соединение»). EventSource сам переподключается
// при разрыве — отдельный reconnect не нужен.
const handlers = new Map<SSEEvent, Set<Handler>>()
let source: EventSource | null = null
const connected = ref(false)

const known = new Set<string>(SSE_EVENTS)

// A handler that throws loses its own event only, not the rest of the turn.
function deliver(event: SSEEvent, data: unknown) {
  handlers.get(event)?.forEach((h) => {
    try {
      h(data)
    } catch (error) {
      console.error(error)
    }
  })
}

// A turn is handed to the handlers in one task, so a burst of events changes the screen once, not once per event.
function dispatchTurn(raw: string) {
  let turn: unknown
  try {
    turn = JSON.parse(raw)
  } catch {
    return // битый кадр — пропускаем
  }
  if (!Array.isArray(turn)) return
  for (const each of turn as { event?: unknown; data?: unknown }[]) {
    if (typeof each?.event === "string" && known.has(each.event)) deliver(each.event as SSEEvent, each.data ?? null)
  }
}

function connect() {
  if (source) return // одно соединение
  source = new EventSource("/api/stream?frames=batch")
  source.onopen = () => {
    connected.value = true
  }
  source.onerror = () => {
    connected.value = false // браузер переподключится сам
  }
  source.addEventListener("batch", (e) => dispatchTurn((e as MessageEvent).data))
}

function disconnect() {
  source?.close()
  source = null
  connected.value = false
}

/** Подписка на SSE-событие. Возвращает функцию отписки. */
function on(event: SSEEvent, handler: Handler): () => void {
  let set = handlers.get(event)
  if (!set) {
    set = new Set()
    handlers.set(event, set)
  }
  set.add(handler)
  return () => {
    handlers.get(event)?.delete(handler)
  }
}

/** Singleton-доступ к SSE-шине: connect() один раз при старте приложения,
 *  on(event, fn) — подписка сторов на события. */
export function useSSE() {
  return { connect, disconnect, on, connected }
}
