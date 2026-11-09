<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue"
import { t } from "../i18n"
import { COMPILATION_COLORS, type CompilationView } from "../lib/compilations"
import type { EpisodeView, SourceMark } from "../lib/output"
import SeasonBlock from "../components/status/output/SeasonBlock.vue"

// The tuning of the colours of an episode put together in part (/#card-colors): a season of the real output tab, as
// cards and as tiles, drawn by its own components over the tokens of styles/tokens.css. The page sets the tokens on
// its sample only; what it prints is the block to put into tokens.css. It reads and writes nothing of the server.

const TRACKS = ["HORIZON", "AniStar", "ChinaTown", "AnimeVost", "FumoDub"]
const READY = 4
const EPISODES = 18
const marks: Record<number, SourceMark> = { 2: { n: 2, color: 1, place: "vkvideo.ru" }, 3: { n: 3, color: 2, place: "vkvideo.ru" } }
// Episodes 1–4 are done in full with HORIZON as the reference; 5–18 have no HORIZON in any source, so they are partial.
const episodes: EpisodeView[] = Array.from({ length: EPISODES }, (_, index) => {
  const episode = index + 1
  const ready = episode <= READY
  const group = ready ? "HORIZON" : "AniStar"
  const source = ready ? 2 : 3
  const rowKey = `sample-${episode}`
  return {
    season: 1, episode, rows: [], levers: [], labels: { "2:vk": "VK", "3:vk": "VK" },
    references: [{ season: 1, episode, part: null, mark: "by_level", source_id: source, row_key: rowKey, player: "vk",
      voice_group: group, height: 2160, reason: null, compilation: null }],
    files: [{ unique_id: rowKey, source_id: source, row_key: rowKey, status: "completed" }],
    dubs: TRACKS.filter((label) => label !== group).map((label) => ({
      season: 1, episode, part: null, voice_key: label, voice_label: label, detail: null, source_id: 3, player: "vk",
      online: true, state: label === "HORIZON" ? "absent" : "done",
    }) as EpisodeView["dubs"][number]),
  }
})
const voices = TRACKS.map((label) => ({ label, episodes: label === "HORIZON" ? `1–${READY}` : `1–${EPISODES}`, online: true }))
const names = episodes.map((view) => ({
  season: 1, episode: view.episode, part: null,
  name: `Series s01e${String(view.episode).padStart(2, "0")} [${view.references[0].voice_group}] VK-DL 2160p.mkv`,
}))

// The colours of the window itself: its states, its source marks, its neutrals; the last ones make sense as stripes.
const GROUNDS = ["#e9ecef", "#f8f9fa", "#dee2e6", "#ced4da", "#6c757d", "#455a64", "#2c3e50", "#4caf50", "#409444", "#2e7d32",
  "#558b2f", "#009688", "#00838f", "#0d6efd", "#3f51b5", "#6a1b9a", "#ad1457", "#dc3545", "#ef6c00", "#b26a00"]
const STRIPES = [...GROUNDS, "#ffffff", "#000000", "#ffc107", "#ffda6a"]

type Hex = string
const rgb = (hex: Hex): number[] => [1, 3, 5].map((at) => parseInt(hex.slice(at, at + 2), 16))
const hexOf = (parts: number[]): Hex => "#" + parts.map((part) => Math.round(Math.max(0, Math.min(255, part))).toString(16).padStart(2, "0")).join("")
const darker = (hex: Hex, share: number): Hex => hexOf(rgb(hex).map((part) => part * (1 - share / 100)))
const toHex = (value: string): Hex => {
  const short = value.trim().toLowerCase()
  return /^#[0-9a-f]{3}$/.test(short) ? "#" + [...short.slice(1)].map((digit) => digit + digit).join("") : short
}

// Stage 12: compilations of the sample season, so that their outlines, letters and colours are tuned on the real cards:
// one takes two episodes that touch only at a corner, others lie side by side, one is a block of four.
const TAKEN: [number, number[]][] = [[0, [2, 7]], [1, [3, 4, 5, 8]], [2, [9]], [3, [15, 16]], [4, [11, 12, 17, 18]]]
const compilations: CompilationView[] = TAKEN.map(([index, taken]) => ({
  season: 1, index, take: true,
  ref: { key: `sample-${index}`, source_id: 2, row_key: `c${index}`, player: "vk", voice_group: "HORIZON", height: 2160,
    start: Math.min(...taken), end: Math.max(...taken) + 1 },
  episodes: taken.map((episode) => ({ episode, member: true, absence: null, elsewhere: null, own: false })),
}))

const root = (name: string): string => getComputedStyle(document.documentElement).getPropertyValue(name).trim()
const current = ref({ ground: "#e9ecef", depth: 7, stripe: "#4caf50", share: 60, colors: [] as string[], line: 0.16, fill: 0.28 })
const state = reactive({ ...current.value, colors: [...current.value.colors] })
const note = ref("")

// What the window shows now is read from its tokens, so the page starts from the real colours and returns to them.
onMounted(() => {
  const ground = toHex(root("--partial-ground"))
  const end = rgb(toHex(root("--partial-ground-end")))
  const depth = Math.round(100 * (1 - end.reduce((sum, part) => sum + part, 0) / Math.max(1, rgb(ground).reduce((sum, part) => sum + part, 0))))
  current.value = { ground, depth, stripe: toHex(root("--partial-stripe")), share: parseFloat(root("--partial-stripe-share")),
    colors: Array.from({ length: COMPILATION_COLORS }, (_, index) => toHex(root(`--compilation-${index + 1}`))),
    line: parseFloat(root("--compilation-line")), fill: parseFloat(root("--compilation-ground-share")) }
  Object.assign(state, { ...current.value, colors: [...current.value.colors] })
})

const end = computed(() => darker(state.ground, state.depth))
const tokens = computed(() => ({
  "--partial-ground": state.ground,
  "--partial-ground-end": end.value,
  "--partial-stripe": state.stripe,
  "--partial-stripe-share": `${state.share}%`,
  ...Object.fromEntries(state.colors.map((colour, index) => [`--compilation-${index + 1}`, colour])),
  "--compilation-line": `${state.line}rem`,
  "--compilation-ground-share": `${state.fill}`,
}))
const block = computed(() => `:root {\n${Object.entries(tokens.value).map(([name, value]) => `  ${name}: ${value};`).join("\n")}\n}`)

function pick(field: "ground" | "stripe", value: string): void {
  state[field] = toHex(value)
  note.value = ""
}
function reset(): void {
  Object.assign(state, { ...current.value, colors: [...current.value.colors] })
  note.value = t("tools.cardColors.resetDone")
}
const output = ref<HTMLElement | null>(null)
async function copy(): Promise<void> {
  try {
    await navigator.clipboard.writeText(block.value)
    note.value = t("tools.cardColors.copied")
  } catch {
    const range = document.createRange()
    if (output.value) range.selectNodeContents(output.value)
    const selection = getSelection()
    selection?.removeAllRanges()
    selection?.addRange(range)
    note.value = t("tools.cardColors.selected")
  }
}
</script>

<template>
  <main class="tune">
    <header class="tune-head">
      <h1 class="tune-title">{{ t("tools.cardColors.title") }}</h1>
      <p class="tune-lead">{{ t("tools.cardColors.lead") }}</p>
    </header>

    <div class="tune-body">
      <div class="tune-side">
        <section class="tune-sheet" aria-labelledby="tune-ground">
          <h2 id="tune-ground" class="tune-label">{{ t("tools.cardColors.ground") }}</h2>
          <div class="tune-swatches" role="group" :aria-label="t('tools.cardColors.ground')">
            <button
              v-for="colour in GROUNDS" :key="colour" type="button" class="tune-swatch" :style="{ background: colour }"
              :aria-label="colour" :title="colour" :aria-pressed="state.ground === colour" @click="pick('ground', colour)"
            />
          </div>
          <label class="tune-row">
            <span>{{ t("tools.cardColors.own") }}</span>
            <input type="color" :value="state.ground" @input="pick('ground', ($event.target as HTMLInputElement).value)" />
            <span class="tune-value">{{ state.ground }}</span>
          </label>
          <label class="tune-row">
            <span>{{ t("tools.cardColors.depth") }}</span>
            <input v-model.number="state.depth" type="range" min="0" max="40" step="1" />
            <span class="tune-value">{{ state.depth }}%</span>
          </label>
        </section>

        <section class="tune-sheet" aria-labelledby="tune-stripe">
          <h2 id="tune-stripe" class="tune-label">{{ t("tools.cardColors.stripe") }}</h2>
          <div class="tune-swatches" role="group" :aria-label="t('tools.cardColors.stripe')">
            <button
              v-for="colour in STRIPES" :key="colour" type="button" class="tune-swatch" :style="{ background: colour }"
              :aria-label="colour" :title="colour" :aria-pressed="state.stripe === colour" @click="pick('stripe', colour)"
            />
          </div>
          <label class="tune-row">
            <span>{{ t("tools.cardColors.own") }}</span>
            <input type="color" :value="state.stripe" @input="pick('stripe', ($event.target as HTMLInputElement).value)" />
            <span class="tune-value">{{ state.stripe }}</span>
          </label>
          <label class="tune-row">
            <span>{{ t("tools.cardColors.share") }}</span>
            <input v-model.number="state.share" type="range" min="0" max="100" step="1" />
            <span class="tune-value">{{ state.share }}%</span>
          </label>
        </section>

        <section class="tune-sheet" aria-labelledby="tune-compilations">
          <h2 id="tune-compilations" class="tune-label">{{ t("tools.cardColors.compilations") }}</h2>
          <div class="tune-swatches" role="group" :aria-label="t('tools.cardColors.compilations')">
            <label v-for="(colour, index) in state.colors" :key="index" class="tune-compilation" :title="colour">
              <input type="color" :value="colour" @input="state.colors[index] = toHex(($event.target as HTMLInputElement).value)" />
            </label>
          </div>
          <label class="tune-row">
            <span>{{ t("tools.cardColors.line") }}</span>
            <input v-model.number="state.line" type="range" min="0.06" max="0.3" step="0.02" />
            <span class="tune-value">{{ state.line }}rem</span>
          </label>
          <label class="tune-row">
            <span>{{ t("tools.cardColors.fill") }}</span>
            <input v-model.number="state.fill" type="range" min="0" max="1" step="0.02" />
            <span class="tune-value">{{ Math.round(state.fill * 100) }}%</span>
          </label>
        </section>

        <section class="tune-sheet" aria-labelledby="tune-rules">
          <h2 id="tune-rules" class="tune-label">{{ t("tools.cardColors.rules") }}</h2>
          <pre ref="output" class="tune-output">{{ block }}</pre>
          <div class="tune-row">
            <button type="button" class="tune-btn is-main" @click="copy">{{ t("tools.cardColors.copy") }}</button>
            <button type="button" class="tune-btn" @click="reset">{{ t("tools.cardColors.reset") }}</button>
            <span class="tune-note" role="status">{{ note }}</span>
          </div>
        </section>
      </div>

      <div class="tune-sample" :style="tokens">
        <SeasonBlock :season="1" :episodes="episodes" :total="EPISODES" :voices="voices" :marks="marks" :names="names" mode="cards"
                     :compilations="compilations" />
        <SeasonBlock :season="1" :episodes="episodes" :total="EPISODES" :voices="voices" :marks="marks" :names="names" mode="tiles"
                     :compilations="compilations" />
      </div>
    </div>
  </main>
</template>

<style scoped>
.tune {
  display: flex; flex-direction: column; gap: 1rem; max-inline-size: 80rem; margin-inline: auto;
  padding: 1.5rem max(1rem, env(safe-area-inset-right, 0rem)) 3rem max(1rem, env(safe-area-inset-left, 0rem));
}
.tune-head { display: flex; flex-direction: column; gap: 0.375rem; }
.tune-title { margin: 0; font-size: 1.35rem; color: #2c3e50; }
.tune-lead { margin: 0; max-inline-size: 65ch; color: var(--text-muted); line-height: 1.5; }
/* The palettes beside the sample while there is room for both, and they stay in view as the sample scrolls. */
.tune-body { display: flex; flex-wrap: wrap; align-items: flex-start; gap: 1rem; }
.tune-side { flex: 1 1 22rem; display: flex; flex-direction: column; gap: 1rem; position: sticky; inset-block-start: 1rem; }
.tune-sample { flex: 999 1 30rem; min-inline-size: min(100%, 30rem); display: flex; flex-direction: column; gap: 1rem; }
.tune-sheet {
  display: flex; flex-direction: column; gap: 0.75rem; padding: 1rem; background: var(--bg-white);
  border-radius: var(--card-border-radius); box-shadow: var(--card-shadow);
}
.tune-label { margin: 0; font-size: 0.8rem; font-weight: 600; letter-spacing: 0.04em; text-transform: uppercase; color: var(--text-muted); }
.tune-swatches { display: grid; grid-template-columns: repeat(auto-fill, minmax(2rem, 1fr)); gap: 0.375rem; }
.tune-swatch {
  all: unset; box-sizing: border-box; aspect-ratio: 1; border-radius: calc(2rem * var(--round)); cursor: pointer;
  border: 0.0625rem solid rgba(0, 0, 0, 0.12);
}
.tune-swatch:focus-visible { outline: 0.125rem solid var(--color-blue); outline-offset: 0.125rem; }
.tune-swatch[aria-pressed="true"] { outline: 0.1875rem solid #2c3e50; outline-offset: 0.125rem; }
.tune-row { display: flex; flex-wrap: wrap; align-items: center; gap: 0.5rem 0.75rem; font-size: 0.85rem; color: var(--text-muted); }
.tune-row input[type="color"] { inline-size: 2.5rem; block-size: 2rem; padding: 0; border: 0.0625rem solid var(--border-color); border-radius: 0.375rem; background: none; cursor: pointer; }
.tune-row input[type="range"] { flex: 1 1 8rem; min-inline-size: 0; accent-color: var(--color-blue); }
.tune-value { min-inline-size: 3.5rem; font-family: var(--font-mono); font-size: 0.8rem; font-variant-numeric: tabular-nums; color: #2c3e50; }
.tune-output {
  margin: 0; overflow-x: auto; padding: 0.75rem; background: var(--bg-light); border: 0.0625rem solid var(--border-color);
  border-radius: 0.5rem; font-family: var(--font-mono); font-size: 0.8rem; line-height: 1.6;
}
.tune-btn {
  border: 0.0625rem solid var(--border-color); background: var(--bg-white); color: #2c3e50; border-radius: 0.5rem;
  padding: 0.4rem 0.8rem; font: inherit; font-size: 0.85rem; cursor: pointer;
}
.tune-btn.is-main { background: #2c3e50; border-color: #2c3e50; color: #fff; }
.tune-btn:focus-visible { outline: 0.125rem solid var(--color-blue); outline-offset: 0.125rem; }
.tune-note { min-block-size: 1.2em; }
.tune-compilation { display: inline-flex; }
.tune-compilation input { inline-size: 2rem; block-size: 2rem; padding: 0; border: none; background: none; cursor: pointer; }
</style>
