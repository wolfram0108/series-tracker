// The page runs the build it was loaded with; a deploy replaces the build on the server and restarts it. A page that outlived
// the restart would show the data of the new core with the code of the old one, so when its stream comes back after a loss it
// asks the page of the server once and loads it again if that names another build.

/** The entry script this page runs, as the build named it (`/assets/index-<hash>.js`); none outside a built page. */
export function ownEntry(doc: Document = document): string | null {
  const script = doc.querySelector<HTMLScriptElement>('script[type="module"][src]')
  return script ? new URL(script.src, doc.baseURI).pathname : null
}

/** Whether the page of the server is another build than the one this page runs. A page that cannot be read says nothing. */
export async function rebuilt(entry: string | null = ownEntry()): Promise<boolean> {
  if (!entry) return false
  try {
    const answer = await fetch("/", { cache: "no-store" })
    return answer.ok && !(await answer.text()).includes(entry)
  } catch {
    return false
  }
}

/** Load the page again when the server serves another build. */
export async function reloadIfRebuilt(): Promise<void> {
  if (await rebuilt()) window.location.reload()
}
