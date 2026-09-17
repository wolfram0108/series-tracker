// Filesystem-safe names: mirror of core/fsnames.py. Both rule sets and their
// test tables must stay identical.
const SEPARATORS = /[\\/]/g
const BACKSLASH = /\\/g
const FORBIDDEN = /[<>:"|?*\x00-\x1f\x7f]/g
// Python's re \s set, spelled out: JS \s differs on \x1c-\x1f, \x85 and \ufeff.
const SPACES = /[\t\n\v\f\r\x1c-\x1f \x85\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+/g

/** Live filter for a name field: drops forbidden characters, keeps whitespace as typed. */
export function stripName(raw: string): string {
  return raw.replace(SEPARATORS, "").replace(FORBIDDEN, "")
}

/** Live filter for a path field: a backslash is a separator typo, not a forbidden character. */
export function stripPath(raw: string): string {
  return raw.replace(BACKSLASH, "/").replace(FORBIDDEN, "")
}

/** Single component: forbidden characters dropped, whitespace collapsed and trimmed. */
export function sanitizeName(name: string): string {
  return stripName(name.replace(SPACES, " ")).replace(SPACES, " ").trim()
}

/** Slash-separated path: every component sanitized; empty components (leading/trailing slash) stay. */
export function sanitizePath(path: string): string {
  return path.replace(BACKSLASH, "/").split("/").map(sanitizeName).join("/")
}

export type FsKind = "filename" | "path"

/** Per-kind pair: `live` runs on every keystroke, `commit` yields the model value. */
export const FS_RULES: Record<FsKind, { live: (raw: string) => string; commit: (value: string) => string }> = {
  filename: { live: stripName, commit: sanitizeName },
  path: { live: stripPath, commit: sanitizePath },
}
