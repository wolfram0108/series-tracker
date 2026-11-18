<script setup lang="ts">
import { computed, ref, watch } from "vue"
import FoldButton from "./FoldButton.vue"
import SheetField from "./SheetField.vue"
import StInput from "./StInput.vue"
import { t } from "../i18n"
import { searchTitles } from "../composables/sourceCalls"
import { reasonText } from "../lib/reason"
import type { TitleCard, TitleLine, TitleSearchReply } from "../lib/sources"

// The search of the titles of the provider of external sources: the user's words (the names of the series offered as buttons),
// a page of the titles as the provider gives them, in its order, and a title added as a source by its address. Nothing is chosen
// for the user: every title the provider gave is shown, with what it says of itself and the lines it has.
const props = withDefaults(defineProps<{
  /** The names of the series the user may search by: its own, the original one of TMDB, those its pages write. */
  names: { text: string; mark?: string }[]
  /** The addresses already sources of the series, with their place in the list. */
  added: Record<string, number>
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

// A provider that does not say how many pages it has has more while a page brings any title.
const more = computed(() => {
  const reply = last.value
  if (!reply || reply.reason) return false
  if (reply.pages != null) return (reply.page ?? 1) < reply.pages
  return (reply.titles?.length ?? 0) > 0
})
const nextPage = computed(() => (last.value?.page ?? 1) + 1)

const found = computed(() => {
  const reply = last.value
  if (!reply) return ""
  if (reply.total != null && reply.pages != null) {
    return t("sources.titles.found", { total: reply.total, page: reply.page ?? 1, pages: reply.pages })
  }
  return t("sources.titles.page", { page: reply.page ?? 1 })
})

function meta(card: TitleCard): string {
  return [card.year, card.area, (card.genres ?? "").split(",").filter(Boolean).join(", ")].filter(Boolean).join(" · ")
}

function lineText(line: TitleLine): string {
  return [line.label || line.code, line.rows != null ? String(line.rows) : ""].filter(Boolean).join(" · ")
}
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
      <header v-if="titles.length" class="ts-head"><span class="muted">{{ found }}</span></header>
    </template>

    <article
      v-for="card in titles" :key="card.id" class="ts-card" :class="{ 'is-added': card.url in added }" :data-card="card.id"
    >
      <img v-if="card.pic" class="ts-poster" :src="card.pic" alt="" loading="lazy" referrerpolicy="no-referrer" />
      <div v-else class="ts-poster" />
      <div class="ts-body">
        <div class="ts-title"><span class="ts-name-text">{{ card.name }}</span></div>
        <div v-if="card.sub" class="ts-sub">{{ card.sub }}</div>
        <div v-if="meta(card)" class="ts-meta">{{ meta(card) }}</div>
        <div v-if="card.remarks"><span class="ts-chip ts-remarks">{{ card.remarks }}</span></div>
        <div v-if="card.lines?.length" class="ts-lines">
          <span v-for="line in card.lines" :key="line.code" class="ts-chip ts-line" :data-line="line.code">{{ lineText(line) }}</span>
        </div>
        <p v-if="card.blurb" class="ts-blurb">{{ card.blurb }}</p>
      </div>
      <div class="ts-act">
        <span v-if="card.url in added" class="ts-added">{{ t("sources.titles.added", { n: added[card.url] }) }}</span>
        <FoldButton v-else :label="t('sources.add.button')" icon="pi pi-plus" severity="success" fold="sm" class="ts-add"
                    :disabled="busy" @click="emit('add', card.url)" />
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
.ts-card.is-added { border-color: var(--color-green-1); background: color-mix(in srgb, var(--color-green-1) 10%, var(--bg-white)); }
.ts-poster {
  flex: none; inline-size: 4rem; aspect-ratio: 2 / 3; object-fit: cover; border-radius: 0.375rem; background: var(--bg-light);
  max-inline-size: 100%; overflow: hidden;
}
.ts-body { flex: 1 1 14rem; display: grid; gap: 0.2rem; min-inline-size: 0; }
.ts-title { display: flex; flex-wrap: wrap; gap: 0.25rem 0.6rem; align-items: baseline; }
.ts-name-text { font-weight: 700; overflow-wrap: anywhere; }
.ts-sub { font-size: 0.875rem; overflow-wrap: anywhere; }
.ts-meta { color: var(--text-muted); font-size: 0.8rem; overflow-wrap: anywhere; }
.ts-lines { display: flex; flex-wrap: wrap; gap: 0.25rem; }
.ts-chip {
  display: inline-block; border: var(--border-width) solid var(--border-color); background: var(--bg-light); border-radius: 999px;
  padding: 0.05rem 0.55rem; font-size: 0.8rem; overflow-wrap: anywhere;
}
.ts-blurb {
  margin: 0; color: var(--text-muted); font-size: 0.8rem; line-height: 1.45; overflow: hidden;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
}
.ts-act { flex: none; margin-inline-start: auto; display: grid; gap: 0.375rem; justify-items: end; }
.ts-added {
  border: var(--border-width) solid var(--color-green-1); color: var(--color-green-1); border-radius: 999px; padding: 0.05rem 0.6rem;
  font-size: 0.8rem; font-weight: 600; white-space: nowrap;
}
.ts-reason { color: var(--color-red-1); overflow-wrap: anywhere; }
.ts-more { align-self: flex-start; }
/* The buttons of a card are those of the head of a source: the main screen's, at the height of a line of text. */
</style>
