// @vitest-environment jsdom
import { describe, expect, it } from "vitest"
import { HELD, LEVER, holdLever } from "./lever"

// A window with a lever and a plain control; the guard sits on the window in the capture phase, as the tab puts it.
function windowOf(): { root: HTMLElement; lever: HTMLInputElement; plain: HTMLButtonElement; reached: string[] } {
  const root = document.createElement("div")
  const label = document.createElement("label")
  label.setAttribute(LEVER, "")
  const lever = document.createElement("input")
  lever.type = "checkbox"
  label.append(lever)
  const plain = document.createElement("button")
  root.append(label, plain)
  document.body.append(root)
  const reached: string[] = []
  for (const name of HELD) root.addEventListener(name, holdLever, { capture: true })
  lever.addEventListener("click", () => reached.push("lever"))
  plain.addEventListener("click", () => reached.push("plain"))
  return { root, lever, plain, reached }
}

describe("holdLever", () => {
  it("keeps a lever as it is and its handler unreached", () => {
    const { lever, reached } = windowOf()
    lever.click()
    expect(lever.checked).toBe(false)
    expect(reached).toEqual([])
  })

  it("lets a control that is no lever work", () => {
    const { plain, reached } = windowOf()
    plain.click()
    expect(reached).toEqual(["plain"])
  })

  it("lets Tab move the focus off a lever and holds the other keys", () => {
    const { lever } = windowOf()
    const tab = new KeyboardEvent("keydown", { key: "Tab", bubbles: true, cancelable: true })
    const space = new KeyboardEvent("keydown", { key: " ", bubbles: true, cancelable: true })
    lever.dispatchEvent(tab)
    lever.dispatchEvent(space)
    expect(tab.defaultPrevented).toBe(false)
    expect(space.defaultPrevented).toBe(true)
  })
})
