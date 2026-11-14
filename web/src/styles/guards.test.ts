import { readdirSync, readFileSync, statSync } from "node:fs"
import { join, relative } from "node:path"
import { describe, expect, it } from "vitest"

// The guards of the build (law-ui): the layout is intrinsic and the code keeps the contract. They fail on what the law forbids; where the
// code still holds some of it, the count is frozen — lower it when some goes, never raise it.
const SRC = new URL("..", import.meta.url).pathname

function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name)
    return statSync(path).isDirectory() ? walk(path) : [path]
  })
}
const all = walk(SRC)
const read = (path: string): string => readFileSync(path, "utf8")
const noComments = (text: string): string => text.replace(/\/\*[\s\S]*?\*\//g, "")

/** The CSS a file holds: the whole file of a stylesheet, the <style> blocks of a component. */
function styleOf(path: string): string {
  const text = read(path)
  if (path.endsWith(".css")) return noComments(text)
  return path.endsWith(".vue") ? [...text.matchAll(/<style[^>]*>([\s\S]*?)<\/style>/g)].map((each) => noComments(each[1])).join("\n") : ""
}
const sheets = all.filter((path) => /\.(css|vue)$/.test(path))
const code = all.filter((path) => /\.(vue|ts)$/.test(path) && !/\.(test|fixtures)\.ts$/.test(path) && !path.endsWith(".d.ts"))
const where = (path: string): string => relative(SRC, path)

describe("the layout", () => {
  it("is asked of no width of the window: a query names the container, a pointer, a motion or a print — never a breakpoint", () => {
    const found = sheets.flatMap((path) => [...styleOf(path).matchAll(/@media\s*([^{]*)\{/g)]
      .filter((each) => /\b(?:min|max)-(?:width|height)\b|\b(?:width|height)\s*[<>=]/.test(each[1]))
      .map((each) => `${where(path)}: @media ${each[1].trim()}`))
    expect(found).toEqual([])
  })

  it("keeps still a window of a user who asked for less motion", () => {
    expect(sheets.map(styleOf).join("\n")).toMatch(/@media\s*\(prefers-reduced-motion:\s*reduce\)/)
  })

  // The sample pages are no part of the screens: they hold their own figures.
  const screens = sheets.filter((path) => !/Gallery|CardColors/.test(path))
  const count = (pattern: RegExp, skip: RegExp | null = null): number =>
    screens.filter((path) => !skip?.test(path)).reduce((sum, path) => sum + (styleOf(path).match(pattern)?.length ?? 0), 0)

  it("holds no more sizes in pixels, colours of its own and `!important` than it did (to be lowered, never raised)", () => {
    expect(count(/\b\d+(?:\.\d+)?px\b/g)).toBeLessThanOrEqual(624)
    expect(count(/#[0-9a-fA-F]{3,8}\b|rgba?\(/g, /tokens\.css$/)).toBeLessThanOrEqual(420)
    expect(count(/!important/g)).toBeLessThanOrEqual(84)
  })
})

describe("the code", () => {
  const offenders = (pattern: RegExp, skip: RegExp | null = null): string[] =>
    code.filter((path) => !skip?.test(path)).flatMap((path) => read(path).split("\n")
      .map((line, index) => ({ line, index })).filter(({ line }) => pattern.test(line))
      .map(({ line, index }) => `${where(path)}:${index + 1}: ${line.trim().slice(0, 100)}`))

  it("asks the contract for every call: the client is typed, no call is cast around it", () => {
    expect(offenders(/\bapi\.[A-Z]+\([^)]*\bas never\b/)).toEqual([])
    expect(offenders(/\bas never\b/, /lib\/layout\.ts$/)).toEqual([])
  })

  it("puts no markup of a text into the page, no timer that polls, no link that opens a tab without noopener", () => {
    expect(offenders(/v-html|innerHTML/)).toEqual([])
    expect(offenders(/\bsetInterval\(/, /Gallery\.vue$/)).toEqual([])
    expect(offenders(/target="_blank"(?![^>]*rel="[^"]*noopener)/)).toEqual([])
  })
})

// What axe cannot see — the words on a gradient ground — is counted here, with the shades axe does see.
function luminance(hex: string): number {
  const channel = (at: number): number => {
    const value = parseInt(hex.slice(at, at + 2), 16) / 255
    return value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4
  }
  return 0.2126 * channel(1) + 0.7152 * channel(3) + 0.0722 * channel(5)
}
function contrast(a: string, b: string): number {
  const [high, low] = [luminance(a), luminance(b)].sort((x, y) => y - x)
  return (high + 0.05) / (low + 0.05)
}

describe("the shades", () => {
  const tokens = noComments(read(join(SRC, "styles/tokens.css")))
  const token = (name: string): string => tokens.match(new RegExp(`${name}:\\s*(#[0-9a-fA-F]{6})`))![1]

  it("keep muted words readable (4.5:1) on the grounds they stand on, and blue words on the blue tint", () => {
    for (const ground of [token("--color-gray-200"), "#f8f9fa", "#ffffff"]) expect(contrast(token("--text-muted"), ground)).toBeGreaterThanOrEqual(4.5)
    expect(contrast(token("--color-blue-ink"), "#cfe2ff")).toBeGreaterThanOrEqual(4.5)
  })

  it("keep the words of every button readable (4.5:1) on both stops of its ground", () => {
    const grounds = [...tokens.matchAll(/--button-(\w+):\s*linear-gradient\(135deg,\s*(#[0-9a-fA-F]{6}) 0%,\s*(#[0-9a-fA-F]{6}) 100%\)/g)]
    expect(grounds.map((each) => each[1])).toEqual(["primary", "secondary", "help", "success", "danger", "warn", "info"])
    for (const [, name, from, to] of grounds) {
      const ink = name === "warn" ? "#212529" : "#ffffff" // the ink the sheet gives the button (styles/overrides.css)
      expect([name, contrast(ink, from) >= 4.5, contrast(ink, to) >= 4.5]).toEqual([name, true, true])
    }
    expect(noComments(read(join(SRC, "styles/overrides.css")))).not.toMatch(/linear-gradient\(135deg,\s*#/) // no ground of a button outside the tokens
  })
})
