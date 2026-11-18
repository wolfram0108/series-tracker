<script setup lang="ts">
import { computed, ref } from "vue"
import { api } from "../../api/client"
import { t } from "../../i18n"
import { useApi } from "../../composables/useApi"

// «Хранение данных источников», folded by default: every site the sources of the series read (VK is one site, an
// application is its key) with how many sources read it and the term of what it answered, hours and minutes.
interface SiteTerm { site: string; app: boolean; sources: number; minutes: number }

const { request } = useApi()
const open = ref(false)
const sites = ref<SiteTerm[] | null>(null)
const groups = computed(() => [
  { key: "sites", label: t("terms.sites"), rows: (sites.value ?? []).filter((each) => !each.app) },
  { key: "apps", label: t("terms.apps"), rows: (sites.value ?? []).filter((each) => each.app) },
].filter((group) => group.rows.length))

async function load(): Promise<void> {
  const got = (await request(api.GET("/api/settings/source_terms"))) as { sites?: SiteTerm[] } | null
  if (got?.sites) sites.value = got.sites
}
async function toggle(): Promise<void> {
  open.value = !open.value
  if (open.value) await load()
}
async function save(row: SiteTerm, hours: string, minutes: string): Promise<void> {
  const total = Number(hours) * 60 + Number(minutes)
  if (!Number.isInteger(total) || total < 1 || total === row.minutes) return void load()
  const saved = await request(api.POST("/api/settings/source_terms", { body: { site: row.site, minutes: total } }),
                              { errorMessage: t("terms.saveFailed") })
  if (saved !== null) row.minutes = total
  else await load()
}
const field = (event: Event): HTMLInputElement => event.target as HTMLInputElement
</script>

<template>
  <section class="terms">
    <button type="button" class="terms-head" :aria-expanded="open" @click="toggle">
      <i class="pi" :class="open ? 'pi-chevron-down' : 'pi-chevron-right'" aria-hidden="true" />{{ t("terms.title") }}
      <span v-if="sites" class="sheet-count">{{ sites.length }}</span>
    </button>
    <table v-if="open && sites" class="terms-table">
      <thead>
        <tr><th scope="col">{{ t("terms.site") }}</th><th scope="col" class="is-number">{{ t("terms.sources") }}</th>
          <th scope="col">{{ t("terms.term") }}</th></tr>
      </thead>
      <tbody v-for="group in groups" :key="group.key">
        <tr class="terms-group"><th colspan="3" scope="colgroup">{{ group.label }}</th></tr>
        <tr v-for="row in group.rows" :key="row.site">
          <td class="terms-site">{{ row.site }}</td>
          <td class="is-number">{{ row.sources }}</td>
          <td>
            <span class="terms-term">
              <input type="number" min="0" :value="Math.floor(row.minutes / 60)" :aria-label="t('terms.hoursOf', { site: row.site })"
                     @change="save(row, field($event).value, String(row.minutes % 60))" /> {{ t("terms.hours") }}
              <input type="number" min="0" max="59" :value="row.minutes % 60" :aria-label="t('terms.minutesOf', { site: row.site })"
                     @change="save(row, String(Math.floor(row.minutes / 60)), field($event).value)" /> {{ t("terms.minutes") }}
            </span>
          </td>
        </tr>
      </tbody>
    </table>
  </section>
</template>

<style scoped>
.terms { display: flex; flex-direction: column; border: 1px solid var(--border-color); border-radius: var(--border-radius); overflow: hidden; }
.terms-head { display: flex; align-items: center; gap: 0.6rem; min-block-size: 2.75rem; padding-inline: 1rem; border: 0;
  background: var(--bg-light); font-weight: 600; text-align: start; cursor: pointer; color: inherit; font-size: 0.95rem; }
.terms-head .sheet-count { margin-inline-start: auto; }
.terms-table { inline-size: 100%; border-collapse: collapse; font-size: 0.9rem; }
.terms-table th, .terms-table td { padding: 0.45rem 1rem; text-align: start; border-block-start: 1px solid var(--border-color); }
.terms-table thead th { font-size: 0.75rem; color: var(--text-muted); }
.terms-group th { font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.04em; color: var(--text-muted); background: var(--bg-light); }
.terms-table .is-number { text-align: end; }
.terms-site { overflow-wrap: anywhere; }
.terms-term { display: inline-flex; align-items: center; gap: 0.35rem; white-space: nowrap; }
.terms-term input { inline-size: 3.5rem; font: inherit; padding: 0.2rem 0.4rem; border: 1px solid var(--border-color); border-radius: 0.4rem; text-align: end; }
</style>
