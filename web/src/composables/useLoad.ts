import { computed, ref } from "vue"

// The state of a read a screen waits for: it is read (`loading`), has been read (`loaded`) or could not be (`failed`). A screen that
// shows its form before it has been read shows empty fields that may be written to and are written over when the data comes; so the
// form is put on show (components/LoadGate.vue) only when `loaded`, and a skeleton of it stands in its place until then.
// Only the first read shows the skeleton: a read that follows (the stream came back, a save was refused) refreshes the screen under
// the user's hands and keeps what is on show if it fails.
export type LoadState = "idle" | "loading" | "loaded" | "failed"

/** `read` answers whether it has read: a refusal or a lost answer is told to the user by the call itself (useApi), here it only decides what stands. */
export function useLoad(read: () => Promise<boolean>) {
  const state = ref<LoadState>("idle")
  const loaded = computed(() => state.value === "loaded")
  let running: Promise<void> | null = null

  async function run(): Promise<void> {
    if (running) return running // one read at a time: a second ask waits for the first
    if (state.value !== "loaded") state.value = "loading"
    running = (async () => {
      let ok = false
      try {
        ok = await read()
      } catch (error) {
        console.error(error)
      }
      if (ok) state.value = "loaded"
      else if (state.value !== "loaded") state.value = "failed"
    })().finally(() => {
      running = null
    })
    return running
  }

  return { state, loaded, run }
}
