import type { Directive } from "vue"

// A block that does what a click on it does, and is no button in the markup, gets the keyboard of a button: the focus reaches it
// by Tab, Enter and Space press it. Where the block holds a field of its own it is no place for this: give it a real <button>.
function onKey(event: KeyboardEvent): void {
  if (event.target !== event.currentTarget || (event.key !== "Enter" && event.key !== " ")) return
  event.preventDefault()
  ;(event.currentTarget as HTMLElement).click()
}

export const vPress: Directive<HTMLElement> = {
  mounted(el) {
    if (!el.hasAttribute("tabindex")) el.tabIndex = 0
    if (!el.hasAttribute("role")) el.setAttribute("role", "button")
    el.addEventListener("keydown", onKey)
  },
  beforeUnmount(el) {
    el.removeEventListener("keydown", onKey)
  },
}
