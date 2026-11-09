// A lever is a control that changes the plan of the series (a switch, a queue, a retry). While a change of the window is
// on its way, its levers take no new one, and they keep their look: a fade of every control on every press reads as a
// flicker. One guard on the window holds them; a lever only declares itself with the attribute.
export const LEVER = "data-lever"

// The events a lever acts on; Tab still moves the focus, so the keyboard does not lose its place while the change is on its way.
export const HELD = ["click", "keydown", "dragstart", "drop"] as const

export function holdLever(event: Event): void {
  if (event instanceof KeyboardEvent && event.key === "Tab") return
  if (!(event.target instanceof Element) || !event.target.closest(`[${LEVER}]`)) return
  event.preventDefault()
  event.stopPropagation()
}
