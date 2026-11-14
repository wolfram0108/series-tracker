<script setup lang="ts">
import SheetRow from "./SheetRow.vue"
import TmdbSearchField from "./TmdbSearchField.vue"
import CatalogNameField from "./CatalogNameField.vue"
import { vPress } from "../directives/press"
import type { TmdbChoice } from "../composables/useTmdbChoice"
import { t, tn } from "../i18n"

// The TMDB row of the window of a series (the one that makes it, the one that edits it): the search by a name, the cards found with the
// one chosen marked, the card chosen when it came from the series itself, and the name the catalog gives its folder. The state is the
// owner's (useTmdbChoice). `seasonless`: the series takes every season, so its card says that instead of the episodes of one.
defineProps<{ tmdb: TmdbChoice; seasonless?: boolean }>()
</script>

<template>
  <SheetRow :caption="t('seriesForm.tmdbSync')">
    <TmdbSearchField v-model="tmdb.query.value" :loading="tmdb.loading.value" @search="tmdb.search" />

    <div v-if="tmdb.results.value.length" class="tmdb-results">
      <div
        v-for="res in tmdb.results.value"
        :key="res.id"
        class="card-final card-tmdb tmdb-result-card"
        :class="tmdb.selected.value && tmdb.selected.value.id === res.id ? 'status-success' : 'status-no-match'"
        @click="tmdb.select(res)"
      >
        <div class="info-column">
          <span class="card-title">{{ tmdb.name(res) }} <small class="tmdb-year">({{ res.year }})</small></span>
          <div class="path-line tmdb-pills">
            <span v-if="res.original_name" class="path-pill">
              <span class="path-pill-label">{{ t("seriesForm.originalLabel") }}</span>
              <span class="path-pill-value">{{ res.original_name }}</span>
            </span>
            <span v-if="tmdb.selected.value && tmdb.selected.value.id === res.id && !seasonless" class="path-pill">
              <span class="path-pill-label">{{ t("seriesForm.seasonPill", { n: tmdb.seasonNumber.value }) }}</span>
              <span class="path-pill-value">{{ tn("seriesForm.episodes", tmdb.episodeCount.value) }}</span>
            </span>
            <span v-else-if="tmdb.selected.value && tmdb.selected.value.id === res.id" class="path-pill">
              <span class="path-pill-value">{{ t("add.severalSeasons") }}</span>
            </span>
          </div>
        </div>
        <div class="pills-column">
          <div class="quality-badge">ID: {{ res.id }}</div>
          <div v-if="tmdb.selected.value && tmdb.selected.value.id === res.id" class="pill">
            <i class="pi pi-check-circle" aria-hidden="true" /><span>{{ t("seriesForm.chosen") }}</span>
          </div>
          <div
            v-if="tmdb.selected.value && tmdb.selected.value.id === res.id" v-press class="pill pill-link" :title="t('add.tmdbClear')"
            @click.stop="tmdb.clear"
          >
            <i class="pi pi-times" aria-hidden="true" /><span>{{ t("seriesForm.clearChoice") }}</span>
          </div>
        </div>
      </div>
    </div>

    <!-- выбранный без поиска (предзаполнен из tmdb_info) -->
    <div v-else-if="tmdb.selected.value" class="card-final card-tmdb status-success">
      <div class="info-column">
        <span class="card-title">{{ tmdb.name(tmdb.selected.value) }} <small class="tmdb-year">({{ tmdb.selected.value.year }})</small></span>
        <div class="path-line">
          <span class="path-pill">
            <span class="path-pill-label">{{ t("seriesForm.seasonPill", { n: tmdb.seasonNumber.value }) }}</span>
            <span class="path-pill-value">{{ tn("seriesForm.episodes", tmdb.episodeCount.value) }}</span>
          </span>
        </div>
      </div>
      <div class="pills-column">
        <div class="quality-badge">ID: {{ tmdb.selected.value.id }}</div>
        <div class="pill"><i class="pi pi-check-circle" aria-hidden="true" /><span>{{ t("seriesForm.chosen") }}</span></div>
        <div v-press class="pill pill-link" :title="t('add.tmdbClear')" @click.stop="tmdb.clear">
          <i class="pi pi-times" aria-hidden="true" /><span>{{ t("seriesForm.clearChoice") }}</span>
        </div>
      </div>
    </div>

    <CatalogNameField v-if="tmdb.selected.value && tmdb.catalogName.value" :name="tmdb.catalogName.value" @copy="tmdb.copyCatalogName" />
  </SheetRow>
</template>
