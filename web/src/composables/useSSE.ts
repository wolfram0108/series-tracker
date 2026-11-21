import { ref } from "vue"
import type { components } from "../api/schema"
import { reloadIfRebuilt } from "../lib/build"

/** The events of the stream are named by the one table of the gateway (SSE_MAP) and reach this type through the schema
 *  of the API. The stream is read in frames of the screen, at most two a second (`frames=batch`, gateway FRAME_SECONDS): `event: batch\ndata: [{event, data}, ...]`, the
 *  events in their order; EventSource ignores the keepalive comments itself. */
export type SSEEvent = components["schemas"]["StreamEvent"]["event"]
type Handler = (data: unknown) => void

// Модуль-синглтон: одно соединение и общий реестр на всё приложение
// (инвариант «одно SSE-соединение»). EventSource сам переподключается
// при разрыве — отдельный reconnect не нужен.
const handlers = new Map<SSEEvent, Set<Handler>>()
let source: EventSource | null = null
const connected = ref(false)
// The stream broke after it had been up, or never came up: the window tells it (components/ConnectionBanner.vue). It ends with the
// next open, and an end the user asked for (disconnect) is no loss.
const lost = ref(false)

// The browser reconnects a stream that was cut by itself, but not one the server refused (a session that ended answers 401): that
// one is closed for good. Then the owner of the login says whether to try again (retryOnlyIf), and the tries are spaced so that a
// server that keeps refusing is not asked in a tight loop — the one place where time is the matter, not the signal.
const EVENT_SOURCE_CLOSED = 2
const RETRY_FIRST_MS = 3000
const RETRY_LAST_MS = 30000
let retryWait = RETRY_FIRST_MS
let retryTimer: ReturnType<typeof setTimeout> | null = null
let mayRetry: (() => Promise<boolean>) | null = null

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
    // The stream came back after a loss: the server may have been restarted with another build of this page.
    if (lost.value) void reloadIfRebuilt()
    lost.value = false
    retryWait = RETRY_FIRST_MS
  }
  source.onerror = () => {
    connected.value = false // браузер переподключится сам
    lost.value = true
    if (source?.readyState === EVENT_SOURCE_CLOSED) giveUp()
  }
  source.addEventListener("batch", (e) => dispatchTurn((e as MessageEvent).data))
}

function giveUp() {
  source?.close()
  source = null
  void (async () => {
    try {
      if (mayRetry && !(await mayRetry())) return
    } catch {
      // the question itself got no answer: the server is away, so try again like any other
    }
    retryTimer = setTimeout(connect, retryWait)
    retryWait = Math.min(retryWait * 2, RETRY_LAST_MS)
  })()
}

function disconnect() {
  if (retryTimer) clearTimeout(retryTimer)
  retryTimer = null
  source?.close()
  source = null
  connected.value = false
  lost.value = false
}

/** Whether a stream that was closed for good is opened again: false where the user has to log in first (the login connects again). */
function retryOnlyIf(check: () => Promise<boolean>): void {
  mayRetry = check
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
  return { connect, disconnect, on, connected, lost, retryOnlyIf }
}
