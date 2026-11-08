/**
 * Reads of the layout of many elements in one go. A read of a size after a write to the page makes the browser lay the
 * whole document out again before it answers; a window of many cards, each reading its sizes when it mounts, lays the
 * document out once per card. Here every read asked for until the next frame runs first, then every write: the document is
 * laid out once for all of them. The frame is the signal of the browser that it is about to draw, not a pause.
 */
type Job = { read: () => unknown; write: (value: never) => void }

let queued: Job[] = []
let asked = false

function flush(): void {
  const jobs = queued
  queued = []
  asked = false
  const values = jobs.map((job) => job.read())
  jobs.forEach((job, index) => job.write(values[index] as never))
}

/** `read` the layout and `write` what it found, batched with every other read of the frame (see the module). */
export function inFrame<T>(read: () => T, write: (value: T) => void): void {
  queued.push({ read, write: write as (value: never) => void })
  if (asked) return
  asked = true
  if (typeof requestAnimationFrame === "function") requestAnimationFrame(flush)
  else flush()
}
