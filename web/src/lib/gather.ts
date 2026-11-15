// Changes made in quick succession, held and sent together once the user pauses: a debounce with a batch. Every change
// restarts the pause, so the count the core makes for them is one, however many were made; a change that undoes a held
// one is no change. What is sent goes one batch after another, in the order the user made them.

/** The clock of the pause; the window's own is the browser's, a test may bring its own. */
export interface GatherTimer {
  set(run: () => void, ms: number): unknown
  clear(handle: unknown): void
}

const BROWSER: GatherTimer = { set: (run, ms) => setTimeout(run, ms), clear: (handle) => clearTimeout(handle as number) }

export interface GatherOptions<C> {
  /** How long the user must be quiet before what is held goes. */
  pause: number
  /** Sends one batch; the words of a refusal, or null when the core took it. */
  send: (changes: C[]) => Promise<string | null>
  /** The held changes, told with every change of them. */
  changed?: (held: ReadonlyMap<string, C>) => void
  timer?: GatherTimer
}

export class Gather<C> {
  private held = new Map<string, C>()
  private handle: unknown = null
  private onTheWay = 0
  // Where the sends stand: a batch waits for the one before it, and a refused one does not stop the ones after it.
  private tail: Promise<string | null> = Promise.resolve(null)
  private readonly timer: GatherTimer

  constructor(private readonly options: GatherOptions<C>) {
    this.timer = options.timer ?? BROWSER
  }

  get size(): number {
    return this.held.size
  }

  /** Nothing is held and nothing is on its way: whoever sends after it need wait for nothing. */
  get idle(): boolean {
    return this.held.size === 0 && this.onTheWay === 0
  }

  /** Holds a change under its key (a later one of the same key takes its place) and restarts the pause. */
  put(key: string, change: C): void {
    this.held.set(key, change)
    this.told()
    this.arm()
  }

  /** Takes a held change back: the user undid it before it went. */
  drop(key: string): void {
    if (!this.held.delete(key)) return
    this.told()
    if (this.held.size) this.arm()
    else this.disarm()
  }

  /** Sends what is held now, after what is on its way; the pause ends. With nothing held it answers when the sends end. */
  flush(): Promise<string | null> {
    this.disarm()
    if (this.held.size === 0) return this.tail
    const batch = [...this.held.values()]
    this.held = new Map()
    this.told()
    this.onTheWay += 1
    const sent = this.tail.then(() => this.options.send(batch))
    this.tail = sent.catch(() => null).finally(() => { this.onTheWay -= 1 })
    return sent
  }

  private arm(): void {
    this.disarm()
    this.handle = this.timer.set(() => {
      this.handle = null
      void this.flush()
    }, this.options.pause)
  }

  private disarm(): void {
    if (this.handle === null) return
    this.timer.clear(this.handle)
    this.handle = null
  }

  private told(): void {
    this.options.changed?.(new Map(this.held))
  }
}
