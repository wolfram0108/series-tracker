import { ref } from "vue"
import type { components } from "../api/schema"

/** The events of the stream are named by the one table of the gateway (SSE_MAP) and reach this type through the schema
 *  of the API. The stream is read in frames of a turn (`frames=batch`): `event: batch\ndata: [{event, data}, ...]`, the
 *  events in their order; EventSource ignores the keepalive comments itself. */
export type SSEEvent = components["schemas"]["StreamEvent"]["event"]
type Handler = (data: unknown) => void

// Модуль-синглтон: одно соединение и общий реестр на всё приложение
// (инвариант «одно SSE-соединение»). EventSource сам переподключается
// при разрыве — отдельный reconnect не нужен.
const handlers = new Map<SSEEvent, Set<Handler>>()
let source: EventSource | null = null
const connected = ref(false)

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
    if (typeof each?.event === "string") deliver(each.event as SSEEvent, each.data ?? null)
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
