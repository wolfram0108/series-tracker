// @vitest-environment jsdom
import { describe, expect, it } from "vitest"
import { flushPromises, mount } from "@vue/test-utils"
import GroupOutline from "./GroupOutline.vue"

function rect(left: number, top: number, width: number, height: number): DOMRect {
  return { left, top, width, height, right: left + width, bottom: top + height, x: left, y: top, toJSON: () => ({}) } as DOMRect
}

// A grid of two cards side by side; its scroll height is that of a layer drawn when the grid was taller.
function grid(height: number, scrollHeight: number): HTMLElement {
  const element = document.createElement("div")
  element.getBoundingClientRect = () => rect(0, 0, 210, height)
  Object.defineProperty(element, "scrollHeight", { value: scrollHeight })
  for (const [at, episode] of [[0, 1], [110, 2]]) {
    const card = document.createElement("div")
    card.className = "episode-card"
    card.dataset.episode = String(episode)
    card.getBoundingClientRect = () => rect(at, 0, 100, 50)
    element.appendChild(card)
  }
  document.body.appendChild(element)
  return element
}

describe("GroupOutline", () => {
  it("is as tall as the grid now, whatever the layer once made the grid scroll to", async () => {
    const w = mount(GroupOutline, { props: { grid: grid(50, 20000), layout: 1, groups: [{ episodes: [1, 2], tone: "var(--compilation-1)" }] } })
    await flushPromises()
    expect(w.get("svg").attributes()).toMatchObject({ width: "210", height: "50" })
  })
})
