<script setup lang="ts">
import { computed, ref, watch } from "vue"
import FoldButton from "./FoldButton.vue"
import StGroup from "./StGroup.vue"
import StIcon from "./StIcon.vue"
import StInput from "./StInput.vue"
import { t } from "../i18n"
import { searchApps } from "../composables/sourceCalls"
import { reasonText } from "../lib/reason"
import type { AppCard, AppResults } from "../lib/sources"

// The search of the video applications: the user's words (the names of the series offered as buttons), a page of the cards
// of each application as it gives them, in its order, and a card added as a source by its address. Nothing is chosen
// for the user: every card the application gave is shown, with what it says of itself.
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
const answers = ref<AppResults[]>([])
// How many cards the last page of each application brought: one that does not say how many pages it has has more while
// a page brings any.
const brought = ref<Record<string, number>>({})

async function find(page = 1): Promise<void> {
  const words = page === 1 ? query.value.trim() : asked.value
  if (!words || searching.value) return
  searching.value = true
  failure.value = ""
  try {
    const answer = await searchApps(words, page)
    if (!answer.ok) {
      failure.value = answer.text
      return
    }
    asked.value = words
    const apps = answer.data.apps ?? []
    brought.value = { ...brought.value, ...Object.fromEntries(apps.map((each) => [each.app, each.cards?.length ?? 0])) }
    if (page === 1) {
      answers.value = apps
      return
    }
    // A next page adds its cards under those of the pages before, application by application.
    answers.value = answers.value.map((before) => {
      const next = apps.find((each) => each.app === before.app)
      return next ? { ...next, cards: [...(before.cards ?? []), ...(next.cards ?? [])] } : before
    })
  } finally {
    searching.value = false
  }
}

function more(results: AppResults): boolean {
  if (results.reason) return false
  if (results.pages != null) return (results.page ?? 1) < results.pages
  return (brought.value[results.app] ?? 0) > 0
}
const anyMore = computed(() => answers.value.some(more))
const nextPage = computed(() => Math.max(1, ...answers.value.map((each) => each.page ?? 1)) + 1)

function found(results: AppResults): string {
  if (results.total != null && results.pages != null) {
    return t("sources.apps.found", { total: results.total, page: results.page ?? 1, pages: results.pages })
  }
  return t("sources.apps.page", { page: results.page ?? 1 })
}

function meta(card: AppCard): string {
  return [card.year, card.area, (card.genres ?? "").split(",").filter(Boolean).join(", ")].filter(Boolean).join(" · ")
}
</script>

<template>
  <div class="app-search stack fold-room">
    <StGroup>
      <StIcon icon="pi pi-search" />
      <StInput v-model="query" class="app-query" :label="t('sources.apps.query')" @keyup.enter="find()" />
      <div class="item-button-group">
        <button type="button" class="btn-icon btn-add btn-text-icon app-find" :disabled="searching || !query.trim()" @click="find()">
          <i class="pi" :class="searching ? 'pi-spin pi-spinner' : 'pi-search'" /><span>{{ t("sources.apps.find") }}</span>
        </button>
      </div>
    </StGroup>
    <div v-if="names.length" class="app-names">
      <span class="muted">{{ t("sources.apps.names") }}</span>
      <button
        v-for="name in names" :key="name.text" type="button" class="app-name" :class="{ active: name.text === query.trim() }"
        @click="query = name.text"
      >
        {{ name.text }}<small v-if="name.mark">{{ name.mark }}</small>
      </button>
    </div>
    <div v-if="failure" class="app-reason">{{ failure }}</div>
    <div v-else-if="asked && !answers.length" class="muted">{{ t("sources.apps.none") }}</div>

    <section v-for="results in answers" :key="results.app" class="app-results stack" :data-app="results.app">
      <header class="app-head"><b>{{ results.label }}</b><span class="muted">{{ found(results) }}</span></header>
      <div v-if="results.reason" class="app-reason">{{ reasonText(results.reason) }}</div>
      <div v-else-if="!results.cards?.length" class="muted">{{ t("sources.apps.empty") }}</div>
      <article
        v-for="card in results.cards" :key="`${card.app}:${card.id}`" class="app-card" :class="{ 'is-added': card.url in added }"
        :data-card="card.id"
      >
        <img v-if="card.pic" class="app-poster" :src="card.pic" alt="" loading="lazy" referrerpolicy="no-referrer" />
        <div v-else class="app-poster" />
        <div class="app-body">
          <div class="app-title"><span class="app-name-text">{{ card.name }}</span><span class="app-id">№ {{ card.id }}</span></div>
          <div v-if="card.sub" class="app-sub">{{ card.sub }}</div>
          <div v-if="meta(card)" class="app-meta">{{ meta(card) }}</div>
          <div v-if="card.remarks"><span class="app-remarks">{{ card.remarks }}</span></div>
          <p v-if="card.blurb" class="app-blurb">{{ card.blurb }}</p>
        </div>
        <div class="app-act">
          <span v-if="card.url in added" class="app-added">{{ t("sources.apps.added", { n: added[card.url] }) }}</span>
          <span v-else-if="!card.url" class="muted">{{ t("sources.apps.noAddress") }}</span>
          <FoldButton v-else :label="t('sources.add.button')" icon="pi pi-plus" severity="success" fold="sm" class="app-add"
                      :disabled="busy" @click="emit('add', card.url)" />
        </div>
      </article>
    </section>
    <FoldButton v-if="anyMore" :label="t('sources.apps.more')" icon="pi pi-angle-down" severity="info" fold="sm" class="app-more"
                :disabled="searching" @click="find(nextPage)" />
  </div>
</template>

<style scoped>
.muted { color: var(--text-muted); font-size: smaller; }
.app-names { display: flex; flex-wrap: wrap; gap: 0.375rem; align-items: center; }
.app-name {
  border: var(--border-width) solid var(--border-color); background: var(--bg-light); border-radius: 999px;
  padding: 0.15rem 0.65rem; font-size: 0.85rem; cursor: pointer;
}
.app-name.active { border-color: var(--color-blue); color: var(--color-blue); }
.app-name small { color: var(--text-muted); margin-inline-start: 0.3rem; }
.app-head { display: flex; flex-wrap: wrap; gap: 0.25rem 0.6rem; align-items: baseline; padding-block-end: 0.3rem; border-bottom: var(--border-width) solid var(--border-color); }
/* The poster, the words of the card and its action in one line that wraps: the action goes under the words when the
   card is narrow, the words never squeeze into a column. */
.app-card {
  display: flex; flex-wrap: wrap; gap: 0.75rem; align-items: flex-start; padding: 0.6rem;
  border: var(--border-width) solid var(--border-color); border-radius: var(--card-border-radius); background: var(--bg-white);
}
.app-card.is-added { border-color: var(--color-green-1); background: color-mix(in srgb, var(--color-green-1) 10%, var(--bg-white)); }
.app-poster {
  flex: none; inline-size: 4rem; aspect-ratio: 2 / 3; object-fit: cover; border-radius: 0.375rem; background: var(--bg-light);
  max-inline-size: 100%; overflow: hidden;
}
.app-body { flex: 1 1 14rem; display: grid; gap: 0.2rem; min-inline-size: 0; }
.app-title { display: flex; flex-wrap: wrap; gap: 0.25rem 0.6rem; align-items: baseline; }
.app-name-text { font-weight: 700; overflow-wrap: anywhere; }
.app-id { color: var(--text-muted); font-size: 0.8rem; font-variant-numeric: tabular-nums; }
.app-sub { font-size: 0.875rem; overflow-wrap: anywhere; }
.app-meta { color: var(--text-muted); font-size: 0.8rem; overflow-wrap: anywhere; }
.app-remarks {
  display: inline-block; border: var(--border-width) solid var(--border-color); background: var(--bg-light); border-radius: 999px;
  padding: 0.05rem 0.55rem; font-size: 0.8rem; overflow-wrap: anywhere;
}
.app-blurb {
  margin: 0; color: var(--text-muted); font-size: 0.8rem; line-height: 1.45; overflow: hidden;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
}
.app-act { flex: none; margin-inline-start: auto; display: grid; gap: 0.375rem; justify-items: end; }
.app-added {
  border: var(--border-width) solid var(--color-green-1); color: var(--color-green-1); border-radius: 999px; padding: 0.05rem 0.6rem;
  font-size: 0.8rem; font-weight: 600; white-space: nowrap;
}
.app-reason { color: var(--color-red-1); overflow-wrap: anywhere; }
.app-more { align-self: flex-start; }
/* The buttons of a card are those of the head of a source: the main screen's, at the height of a line of text. */
</style>
