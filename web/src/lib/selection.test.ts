import { describe, expect, it } from "vitest"
import { NO_SELECTION, keyOf, pickOf, picked, selectedOf, switchesOff, type Selection } from "./selection"

const SHOWN = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
const keys = (selection: Selection) => [...selection.keys].sort()

describe("selection of episodes", () => {
  it("takes one episode by a click, and a click elsewhere takes that one instead", () => {
    const first = picked(NO_SELECTION, 1, 3, "one", SHOWN)
    expect(keys(first)).toEqual(["1:3"])
    expect(keys(picked(first, 1, 7, "one", SHOWN))).toEqual(["1:7"])
  })

  it("takes the run from the episode clicked before to the one Shift+clicked, either way, in the order shown", () => {
    const anchored = picked(NO_SELECTION, 1, 8, "one", SHOWN)
    expect(keys(picked(anchored, 1, 3, "run", SHOWN))).toEqual(["1:3", "1:4", "1:5", "1:6", "1:7", "1:8"])
    // A filter hides some: the run is of what the screen shows.
    expect(keys(picked(anchored, 1, 3, "run", [3, 5, 8, 9]))).toEqual(["1:3", "1:5", "1:8"])
  })

  it("keeps the start of the run: a second Shift+click makes the run from it again", () => {
    const run = picked(picked(NO_SELECTION, 1, 5, "one", SHOWN), 1, 9, "run", SHOWN)
    expect(keys(picked(run, 1, 2, "run", SHOWN))).toEqual(["1:2", "1:3", "1:4", "1:5"])
  })

  it("adds an episode by Ctrl+click and takes it out by another, and the next run starts from it", () => {
    const run = picked(picked(NO_SELECTION, 1, 2, "one", SHOWN), 1, 4, "run", SHOWN)
    const added = picked(run, 1, 9, "toggle", SHOWN)
    expect(keys(added)).toEqual(["1:2", "1:3", "1:4", "1:9"])
    expect(keys(picked(added, 1, 3, "toggle", SHOWN))).toEqual(["1:2", "1:4", "1:9"])
    expect(keys(picked(added, 1, 7, "run", SHOWN))).toEqual(["1:7", "1:8", "1:9"])
  })

  it("makes no run across seasons or with nothing clicked before: the Shift+click takes the one episode", () => {
    expect(keys(picked(NO_SELECTION, 1, 4, "run", SHOWN))).toEqual(["1:4"])
    expect(keys(picked(picked(NO_SELECTION, 1, 2, "one", SHOWN), 2, 6, "run", SHOWN))).toEqual(["2:6"])
  })

  it("reads the keys held while clicking", () => {
    expect(pickOf({ shiftKey: true, ctrlKey: true, metaKey: false })).toBe("run")
    expect(pickOf({ shiftKey: false, ctrlKey: true, metaKey: false })).toBe("toggle")
    expect(pickOf({ shiftKey: false, ctrlKey: false, metaKey: true })).toBe("toggle")
    expect(pickOf({ shiftKey: false, ctrlKey: false, metaKey: false })).toBe("one")
  })

  it("gives the episodes of a season it holds, in the order asked", () => {
    const selection: Selection = { keys: new Set([keyOf(1, 9), keyOf(2, 1), keyOf(1, 2)]), anchor: null }
    expect(selectedOf(selection, 1, SHOWN)).toEqual([2, 9])
    expect(selectedOf(selection, 3, SHOWN)).toEqual([])
  })

  it("switches the selection off when any of it is on, and on when all of it is off", () => {
    expect(switchesOff([true, false, false])).toBe(true)
    expect(switchesOff([true, true])).toBe(true)
    expect(switchesOff([false, false])).toBe(false)
  })
})
