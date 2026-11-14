import { computed, ref, watch } from "vue"
import { useToast } from "primevue/usetoast"
import { api } from "../api/client"
import { reasonText } from "../lib/reason"
import { t } from "../i18n"

export interface TmdbResult { id: number; name?: string; name_en?: string; original_name?: string; year?: string; poster_path?: string }
export interface TmdbSeason { season_number: number; episode_count: number }

/** The TMDB card of a series: the search by its name, the card chosen with the number of the episodes of its season, and the name of the
 *  folder the catalog gives it. One place for the window that makes a series and the one that edits it (components/TmdbSync.vue shows it).
 *  `query` is what the search asks when the field is empty; `season` and `seasonless` are the series' own, as the form holds them. */
export function useTmdbChoice(host: { query: () => string; season: () => string; seasonless: () => boolean }) {
  const toast = useToast()
  const query = ref("")
  const results = ref<TmdbResult[]>([])
  const selected = ref<TmdbResult | null>(null)
  const loading = ref(false)
  const episodeCount = ref(0)
  const seasonDetails = ref<TmdbSeason[]>([])

  const seasonNumber = computed(() => {
    if (host.seasonless()) return 1
    const m = host.season().match(/^s(\d+)$/i)
    return m ? parseInt(m[1], 10) : 1
  })

  // Отображаемое имя: русское, если перевод реально есть (name ≠ original); иначе английское (для азиатских релизов без ru TMDB
  // кладёт в name оригинал-иероглифы); иначе — что есть. Применяется и в списке, и в имени каталога.
  function name(r: TmdbResult): string {
    const ru = (r.name || "").trim()
    const orig = (r.original_name || "").trim()
    if (ru && ru !== orig) return ru
    const en = (r.name_en || "").trim()
    if (en && en !== orig) return en
    return ru || en || orig
  }

  // «Имя (год) [tmdbid-XXXX]» (шаблон Jellyfin/Plex); год опускается, если его нет
  const catalogName = computed(() => {
    if (!selected.value) return ""
    const shown = name(selected.value)
    if (!shown) return ""
    const year = String(selected.value.year || "").trim()
    const idPart = `[tmdbid-${selected.value.id}]`
    return year ? `${shown} (${year}) ${idPart}` : `${shown} ${idPart}`
  })

  async function search(): Promise<void> {
    if (!query.value) query.value = host.query()
    if (!query.value) return
    loading.value = true
    results.value = []
    try {
      const { data } = await api.POST("/api/tmdb/search", { body: { query: query.value } })
      const res = (data ?? null) as { success?: boolean; results?: TmdbResult[]; error?: string } | null
      if (res?.success) {
        results.value = res.results ?? []
      } else {
        toast.add({ severity: "warn", summary: "TMDB", detail: t("seriesForm.tmdbSearchFailed", { error: reasonText(res?.error) || t("add.unknown") }), life: 4000 })
      }
    } finally {
      loading.value = false
    }
  }

  function updateEpisodeCount(): void {
    if (!selected.value || !seasonDetails.value.length) return
    const season = seasonDetails.value.find((each) => each.season_number === seasonNumber.value)
    episodeCount.value = season ? season.episode_count : 0
  }

  async function select(card: TmdbResult): Promise<void> {
    selected.value = card
    const { data } = await api.GET("/api/tmdb/details/{tmdb_id}", { params: { path: { tmdb_id: card.id } } })
    const res = (data ?? null) as { success?: boolean; seasons?: TmdbSeason[] } | null
    if (res?.success) {
      seasonDetails.value = res.seasons ?? []
      updateEpisodeCount()
    }
  }

  function clear(): void {
    selected.value = null
    seasonDetails.value = []
    episodeCount.value = 0
  }

  watch(host.season, updateEpisodeCount)

  async function copyCatalogName(): Promise<void> {
    if (!catalogName.value) return
    try {
      await navigator.clipboard.writeText(catalogName.value)
      toast.add({ severity: "success", summary: t("common.copied"), detail: t("seriesForm.catalogCopied"), life: 2500 })
    } catch {
      toast.add({ severity: "error", summary: t("common.error"), detail: t("common.copyFailed"), life: 2500 })
    }
  }

  return { query, results, selected, loading, episodeCount, seasonNumber, name, catalogName, search, select, clear, copyCatalogName }
}

export type TmdbChoice = ReturnType<typeof useTmdbChoice>
