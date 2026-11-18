<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue"
import FoldButton from "./FoldButton.vue"
import SheetField from "./SheetField.vue"
import StInput from "./StInput.vue"
import { t, tn } from "../i18n"
import { searchTitles } from "../composables/sourceCalls"
import { reasonText } from "../lib/reason"
import { lineAddress, type TitleCard, type TitleLine, type TitleSearchReply } from "../lib/sources"
import { useProviderStore } from "../stores/provider"

// The search of the titles of the provider of external sources: the user's words (the names of the series offered as buttons),
// a page of the titles as the provider gives them, in its order, and a title added as a source by its address. Nothing is chosen
// for the user: every title the provider gave is shown, with what it says of itself and the lines it has.
const props = withDefaults(defineProps<{
  /** The names of the series the user may search by: its own, the original one of TMDB, those its pages write. */
  names: { text: string; mark?: string }[]
  /** The addresses already sources of the series, with their place in the list and the colour of their mark. */
  added: Record<string, { n: number; color: number | null }>
  busy?: boolean
}>(), { busy: false })
const emit = defineEmits<{ (e: "add", url: string): void }>()

const query = ref(props.names[0]?.text ?? "")
// The name the field starts with is the first one the window knows; a name that comes later fills an empty field.
watch(() => props.names[0]?.text, (first) => {
  if (first && !query.value.trim()) query.value = first
})
const asked = ref("")
const searching = ref(false)
const failure = ref("")
const titles = ref<TitleCard[]>([])
// The last page the provider answered: its counts, its notes and the reason it gave none.
const last = ref<TitleSearchReply | null>(null)

async function find(page = 1): Promise<void> {
  const words = page === 1 ? query.value.trim() : asked.value
  if (!words || searching.value) return
  searching.value = true
  failure.value = ""
  try {
    const answer = await searchTitles(words, page)
    if (!answer.ok) {
      failure.value = answer.text
      return
    }
    asked.value = words
    last.value = answer.data
    const brought = answer.data.titles ?? []
    // A next page adds its titles under those of the pages before.
    titles.value = page === 1 ? brought : [...titles.value, ...brought.filter((each) => !titles.value.some((had) => had.id === each.id))]
  } finally {
    searching.value = false
  }
}

// A provider that says whether a page follows is believed (a page it left bare may still be followed by one that is not); one that
// does not say how many pages it has has more while a page brings any title.
const more = computed(() => {
  const reply = last.value
  if (!reply || reply.reason) return false
  if (reply.more != null) return reply.more
  if (reply.pages != null) return (reply.page ?? 1) < reply.pages
  return (reply.titles?.length ?? 0) > 0
})
const nextPage = computed(() => (last.value?.page ?? 1) + 1)

// What the provider left out of the page (titles with no line it plays) is said, so that nothing is lost in silence.
const withoutLines = computed(() => last.value?.without_lines ?? 0)
const found = computed(() => {
  const reply = last.value
  if (!reply) return ""
  const count = reply.total != null && reply.pages != null
    ? t("sources.titles.found", { total: reply.total, page: reply.page ?? 1, pages: reply.pages })
    : t("sources.titles.page", { page: reply.page ?? 1 })
  return withoutLines.value ? `${count} · ${t("sources.titles.withoutLines", { n: withoutLines.value })}` : count
})

function meta(card: TitleCard): string {
  return [card.year, card.area, (card.genres ?? "").split(",").filter(Boolean).join(", ")].filter(Boolean).join(" · ")
}

// A line is named by its code, the name of the source; how many rows it holds is said in words, and when the provider does not
// know it, that is said, never a bare number or a zero.
function rowsText(line: TitleLine): string {
  return line.rows != null ? tn("sources.titles.rows", line.rows) : t("sources.titles.rowsUnknown")
}

// Whose titles these are: the sites of the lines found, named as the provider's manifest names them for people.
const provider = useProviderStore()
onMounted(() => { void provider.ensure() })
const providerName = computed(() => {
  const sites = new Set(titles.value.flatMap((card) => (card.lines ?? []).map((line) => lineAddress(line.url)?.site ?? "")).filter(Boolean))
  const named = [...sites].map((site) => provider.labelOf(site))
  return named.length ? named.join(", ") : (provider.view?.sites?.[0]?.label ?? "")
})

// The mark of a source added: its number in the colour the series gave it.
const markStyle = (color: number | null) => ({ backgroundColor: color === null ? "var(--text-muted)" : `var(--source-${color % 16})` })
</script>

<template>
  <div class="title-search stack fold-room">
    <SheetField :label="t('sources.titles.query')" icon="pi pi-search">
      <StInput v-model="query" class="ts-query" @keyup.enter="find()" />
      <div class="item-button-group">
        <button type="button" class="btn-icon btn-add btn-text-icon ts-find" :disabled="searching || !query.trim()" @click="find()">
          <i class="pi" :class="searching ? 'pi-spin pi-spinner' : 'pi-search'" /><span>{{ t("sources.titles.find") }}</span>
        </button>
      </div>
    </SheetField>
    <div v-if="names.length" class="ts-names">
      <span class="muted">{{ t("sources.titles.names") }}</span>
      <button
        v-for="name in names" :key="name.text" type="button" class="ts-name" :class="{ active: name.text === query.trim() }"
        @click="query = name.text"
      >
        {{ name.text }}<small v-if="name.mark">{{ name.mark }}</small>
      </button>
    </div>
    <div v-if="failure" class="ts-reason">{{ failure }}</div>
    <template v-else-if="last">
      <div v-if="last.reason" class="ts-reason">{{ reasonText(last.reason) }}</div>
      <div v-for="note in last.notes ?? []" :key="note" class="ts-reason">{{ reasonText(note) }}</div>
      <div v-if="!titles.length && !last.reason" class="muted">{{ t("sources.titles.empty") }}</div>
      <header v-if="titles.length || withoutLines" class="ts-head">
        <span v-if="providerName" class="sheet-caption ts-provider">{{ providerName }}</span><span class="muted">{{ found }}</span>
      </header>
    </template>

    <article v-for="card in titles" :key="card.id" class="ts-card" :data-card="card.id">
      <img v-if="card.pic" class="ts-poster" :src="card.pic" alt="" loading="lazy" referrerpolicy="no-referrer" />
      <div v-else class="ts-poster" />
      <div class="ts-body">
        <div class="ts-title"><span class="ts-name-text">{{ card.name }}</span><span class="muted">{{ card.year }}</span></div>
        <div v-if="card.sub" class="ts-sub">{{ card.sub }}</div>
        <div v-if="meta(card)" class="ts-meta">{{ meta(card) }}</div>
        <div v-if="card.remarks"><span class="ts-chip ts-remarks">{{ card.remarks }}</span></div>
        <p v-if="card.blurb" class="ts-blurb">{{ card.blurb }}</p>
        <!-- A title is no source: each line of it is, a row of its own with its name, how much it holds and its button. -->
        <div v-if="card.lines?.length" class="ts-lines">
          <p class="sheet-caption">{{ t("sources.titles.lines", { n: card.lines?.length ?? 0 }) }}</p>
          <div v-for="line in card.lines" :key="line.code" class="input-constructor-group ts-line" :data-line="line.code"
               :class="{ 'is-added': line.url in added }">
            <div class="constructor-item item-label-text-icon"><i class="pi pi-video" aria-hidden="true" /><b class="ts-code">{{ line.code }}</b></div>
            <div class="constructor-item item-value ts-rows" :class="{ 'is-unknown': line.rows == null }">{{ rowsText(line) }}</div>
            <div v-if="line.url in added" class="constructor-item ts-added">
              <span class="figure-mark" :style="markStyle(added[line.url].color)">{{ added[line.url].n }}</span>{{ t("sources.titles.added", { n: added[line.url].n }) }}
            </div>
            <div v-else class="item-button-group">
              <button type="button" class="btn-icon btn-add btn-text-icon ts-add" :disabled="busy" :title="t('sources.add.button')"
                      :aria-label="t('sources.add.button')" @click="emit('add', line.url)">
                <i class="pi pi-plus" aria-hidden="true" /><span class="ts-add-word">{{ t("sources.add.button") }}</span>
              </button>
            </div>
          </div>
        </div>
      </div>
    </article>
    <FoldButton v-if="more" :label="t('sources.titles.more')" icon="pi pi-angle-down" severity="info" fold="sm" class="ts-more"
                :disabled="searching" @click="find(nextPage)" />
  </div>
</template>

<style scoped>
.muted { color: var(--text-muted); font-size: smaller; }
.ts-names { display: flex; flex-wrap: wrap; gap: 0.375rem; align-items: center; }
.ts-name {
  border: var(--border-width) solid var(--border-color); background: var(--bg-light); border-radius: 999px;
  padding: 0.15rem 0.65rem; font-size: 0.85rem; cursor: pointer;
}
.ts-name.active { border-color: var(--color-blue); color: var(--color-blue); }
.ts-name small { color: var(--text-muted); margin-inline-start: 0.3rem; }
.ts-head { display: flex; flex-wrap: wrap; gap: 0.25rem 0.6rem; align-items: baseline; padding-block-end: 0.3rem; border-bottom: var(--border-width) solid var(--border-color); }
/* The poster, the words of the title and its action in one line that wraps: the action goes under the words when the
   card is narrow, the words never squeeze into a column. */
.ts-card {
  display: flex; flex-wrap: wrap; gap: 0.75rem; align-items: flex-start; padding: 0.6rem;
  border: var(--border-width) solid var(--border-color); border-radius: var(--card-border-radius); background: var(--bg-white);
}
.ts-poster {
  flex: none; inline-size: 4rem; aspect-ratio: 2 / 3; object-fit: cover; border-radius: 0.375rem; background: var(--bg-light);
  max-inline-size: 100%; overflow: hidden;
}
.ts-body { flex: 1 1 14rem; display: grid; gap: 0.2rem; min-inline-size: 0; }
.ts-title { display: flex; flex-wrap: wrap; gap: 0.25rem 0.6rem; align-items: baseline; }
.ts-name-text { font-weight: 700; overflow-wrap: anywhere; }
.ts-sub { font-size: 0.875rem; overflow-wrap: anywhere; }
.ts-meta { color: var(--text-muted); font-size: 0.8rem; overflow-wrap: anywhere; }
.ts-lines { display: grid; gap: 0.35rem; margin-block-start: 0.4rem; }
/* A line is a group of the sheet: its name, how much it holds, and what can be done with it (add it, or its place as a source). */
.ts-line { min-block-size: 2.5rem; flex-wrap: wrap; }
.ts-code { font-weight: 700; color: var(--color-text); }
.ts-rows { font-variant-numeric: tabular-nums; white-space: nowrap; }
.ts-rows.is-unknown { color: var(--text-muted); }
.ts-added {
  gap: 0.5rem; padding: 0 0.9rem; color: color-mix(in srgb, var(--color-green-1) 85%, black); font-weight: 600;
  background: color-mix(in srgb, var(--color-green-1) 10%, transparent); white-space: nowrap;
}
.ts-provider { margin: 0; }
/* Where the room is short (a phone) the button of a line is its icon alone, and the mark of a source added takes the row under the line. */
@container fold-room (inline-size < 30rem) {
  .ts-add-word { display: none; }
  .ts-add { inline-size: 2.75rem; padding: 0; gap: 0; }
  .ts-added { flex: 1 1 100%; }
}
.ts-chip {
  display: inline-block; border: var(--border-width) solid var(--border-color); background: var(--bg-light); border-radius: 999px;
  padding: 0.05rem 0.55rem; font-size: 0.8rem; overflow-wrap: anywhere;
}
.ts-blurb {
  margin: 0; color: var(--text-muted); font-size: 0.8rem; line-height: 1.45; overflow: hidden;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
}
.ts-reason { color: var(--color-red-1); overflow-wrap: anywhere; }
.ts-more { align-self: flex-start; }
/* The buttons of a card are those of the head of a source: the main screen's, at the height of a line of text. */
</style>
