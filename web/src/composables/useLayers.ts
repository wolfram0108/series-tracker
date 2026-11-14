import { onBeforeUnmount, onMounted, useId, type Ref } from "vue"

// The layers of the screen that take the keys: a window, a menu, an open list. A key belongs to the layer on top: Escape
// closes it alone, Tab stays inside it. So a confirmation over a window, or a list inside a window, is never closed together
// with what lies under it. An element that uses Escape for itself (a name being edited) says so with preventDefault on its
// keydown, and then no layer takes the key.
interface Layer {
  escape: () => void
  room: (() => HTMLElement | null) | null
}

const stack: Layer[] = []

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), '
  + 'textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'

function focusable(room: HTMLElement): HTMLElement[] {
  // checkVisibility drops what a closed section or a hidden tab holds; where it is missing (a test without layout) all stay.
  return [...room.querySelectorAll<HTMLElement>(FOCUSABLE)]
    .filter((each) => typeof each.checkVisibility !== "function" || each.checkVisibility())
}

export function hasFocusable(room: HTMLElement): boolean {
  return focusable(room).length > 0
}

/** Tab walks the elements of the room in a ring, whatever the focus was when it began: outside the room it enters it. */
function keepInside(event: KeyboardEvent, room: HTMLElement): void {
  const items = focusable(room)
  const active = document.activeElement
  if (!items.length) {
    event.preventDefault()
    room.focus()
    return
  }
  const first = items[0]
  const last = items[items.length - 1]
  if (!room.contains(active)) {
    event.preventDefault()
    ;(event.shiftKey ? last : first).focus()
  } else if (event.shiftKey && (active === first || active === room)) {
    event.preventDefault()
    last.focus()
  } else if (!event.shiftKey && active === last) {
    event.preventDefault()
    first.focus()
  }
}

function onKey(event: KeyboardEvent): void {
  const top = stack[stack.length - 1]
  if (!top || event.defaultPrevented || event.isComposing) return
  if (event.key === "Escape") {
    event.preventDefault()
    top.escape()
    return
  }
  const room = event.key === "Tab" ? top.room?.() : null
  if (room) keepInside(event, room)
}

function push(layer: Layer): () => void {
  if (!stack.length) document.addEventListener("keydown", onKey)
  stack.push(layer)
  return () => {
    const at = stack.indexOf(layer)
    if (at >= 0) stack.splice(at, 1)
    if (!stack.length) document.removeEventListener("keydown", onKey)
  }
}

/** A popup (a menu, a list) is a layer while it is open: Escape closes it and nothing under it. It leaves with its component. */
export function useLayer(escape: () => void): { enter: () => void; leave: () => void } {
  let leaveNow: (() => void) | null = null
  const enter = (): void => {
    if (!leaveNow) leaveNow = push({ escape, room: null })
  }
  const leave = (): void => {
    leaveNow?.()
    leaveNow = null
  }
  onBeforeUnmount(leave)
  return { enter, leave }
}

/** A window is a layer from its mount to its unmount: the focus goes into it (to the window itself, or to its first element
 *  with `first`), Tab keeps to it, and the focus goes back to what opened it when it closes. `titleId` names it for aria-labelledby. */
export function useDialog(room: Ref<HTMLElement | null>, escape: () => void, options: { first?: boolean } = {}): { titleId: string } {
  const titleId = useId()
  const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null
  let leave: (() => void) | null = null
  onMounted(() => {
    leave = push({ escape, room: () => room.value })
    const inside = room.value
    if (!inside || inside.contains(document.activeElement)) return
    const target = inside.querySelector<HTMLElement>("[autofocus]") ?? (options.first ? focusable(inside)[0] : null) ?? inside
    target.focus({ preventScroll: true })
  })
  onBeforeUnmount(() => {
    leave?.()
    leave = null
    if (opener?.isConnected) opener.focus({ preventScroll: true })
  })
  return { titleId }
}
