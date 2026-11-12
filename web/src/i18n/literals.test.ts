import { readdirSync, readFileSync, statSync } from "node:fs"
import { join, relative } from "node:path"
import { describe, expect, it } from "vitest"

// The guard of the catalog (law-ui): a text the user reads lives in i18n/ru.ts and the code asks for its key. Cyrillic in
// the code of a screen outside a comment is a text that went past the catalog. Sample data lives in *.fixtures.ts.
const SRC = new URL("..", import.meta.url).pathname

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name)
    if (statSync(path).isDirectory()) return name === "i18n" ? [] : sources(path)
    return /\.(vue|ts)$/.test(name) && !/\.(test|fixtures)\.ts$/.test(name) && !name.endsWith(".d.ts") ? [path] : []
  })
}

/** The code of a file without its comments (block, HTML and line ones), its lines where they were. */
function code(text: string): string {
  const blank = (comment: string) => comment.replace(/[^\n]/g, "")
  return text
    .replace(/\/\*[\s\S]*?\*\//g, blank)
    .replace(/<!--[\s\S]*?-->/g, blank)
    .replace(/(^|[^:"'`])\/\/.*$/gm, "$1")
}

describe("the texts of the window", () => {
  it("are keys of the catalog: no screen holds a text of its own", () => {
    const found = sources(SRC).flatMap((path) => code(readFileSync(path, "utf8")).split("\n")
      .map((line, index) => ({ line, index }))
      .filter(({ line }) => /[А-Яа-яЁё]/.test(line))
      .map(({ line, index }) => `${relative(SRC, path)}:${index + 1}: ${line.trim()}`))
    expect(found).toEqual([])
  })
})
