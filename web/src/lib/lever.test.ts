// @vitest-environment jsdom
import { describe, expect, it } from "vitest"
import { HELD, LEVER, LEVER_FREE, holdLever } from "./lever"

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

describe("holdLever and a free lever", () => {
  // A row that is a lever, with a box in it that declares itself free: the box takes a press the row would hold.
  function rowOf() {
    const root = document.createElement("div")
    const row = document.createElement("div")
    row.setAttribute(LEVER, "")
    const box = document.createElement("input")
    box.type = "checkbox"
    box.setAttribute(LEVER_FREE, "")
    const handle = document.createElement("button")
    row.append(box, handle)
    root.append(row)
    document.body.append(root)
    const reached: string[] = []
    for (const name of HELD) root.addEventListener(name, holdLever, { capture: true })
    box.addEventListener("click", () => reached.push("box"))
    handle.addEventListener("click", () => reached.push("handle"))
    return { box, handle, reached }
  }

  it("lets a free lever work inside a lever that is held, and holds the rest of the row", () => {
    const { box, handle, reached } = rowOf()
    box.click()
    handle.click()
    expect(box.checked).toBe(true)
    expect(reached).toEqual(["box"])
  })
})
