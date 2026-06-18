import { ref } from "vue"

/** 11 SSE-событий контракта (из gateway SSE_MAP). Бэк шлёт кадры
 *  `event: <name>\ndata: <json>\n\n`; keepalive-комментарии EventSource
 *  игнорирует сам. */
export const SSE_EVENTS = [
  "series_updated",
  "series_added",
  "series_deleted",
  "agent_queue_update",
  "torrent_progress_update",
  "download_queue_update",
  "slicing_queue_update",
  "scanner_status_update",
  "renaming_complete",
  "relocation_started",
  "relocation_finished",
] as const

export type SSEEvent = (typeof SSE_EVENTS)[number]
type Handler = (data: unknown) => void

// Модуль-синглтон: одно соединение и общий реестр на всё приложение
// (инвариант «одно SSE-соединение»). EventSource сам переподключается
// при разрыве — отдельный reconnect не нужен.
const handlers = new Map<SSEEvent, Set<Handler>>()
let source: EventSource | null = null
const connected = ref(false)

function dispatch(event: SSEEvent, raw: string) {
  let data: unknown = null
  try {
    data = raw ? JSON.parse(raw) : null
  } catch {
    return // битый кадр — пропускаем
  }
  handlers.get(event)?.forEach((h) => h(data))
}

function connect() {
  if (source) return // одно соединение
  source = new EventSource("/api/stream")
  source.onopen = () => {
    connected.value = true
  }
  source.onerror = () => {
    connected.value = false // браузер переподключится сам
  }
  for (const ev of SSE_EVENTS) {
    source.addEventListener(ev, (e) => dispatch(ev, (e as MessageEvent).data))
  }
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
