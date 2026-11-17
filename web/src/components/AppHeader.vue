<script setup lang="ts">
import Button from "primevue/button"
import FoldButton from "./FoldButton.vue"
import ScanClock from "./ScanClock.vue"
import { useIndicatorsStore } from "../stores/indicators"
import { useAuthStore } from "../stores/auth"
import { t } from "../i18n"

// Шапка приложения: заголовок + индикаторы агентов (реактивно из стора) +
// кнопки действий (открытие модалок — обрабатывает родитель).
// The buttons with a word fold to their icon before the header is too narrow to hold them in its row (FoldButton, the header is its room):
// four words are held from 66rem, the tier `xl`.
const ind = useIndicatorsStore()
const auth = useAuthStore()
defineEmits<{ (e: "add"): void; (e: "logs"): void; (e: "extensions"): void; (e: "settings"): void; (e: "logout"): void }>()
</script>

<template>
  <header class="page-header fold-room">
    <div class="page-title-container">
      <h1 class="page-title">WOLFRAM TS</h1>
      <div class="agent-indicators">
        <div class="indicator" :class="{ 'ind-active': ind.monitoring }" :title="t('agents.torrents.title')"></div>
        <div class="indicator ind-small" :class="{ 'ind-active': ind.downloader }" :title="t('download.title')"></div>
      </div>
      <ScanClock />
    </div>
    <div class="header-actions">
      <FoldButton :label="t('header.add')" :hint="t('header.addSeries')" icon="pi pi-plus" fold="xl" @click="$emit('add')" />
      <FoldButton :label="t('header.log')" :hint="t('header.logs')" icon="pi pi-book" severity="secondary" fold="xl" @click="$emit('logs')" />
      <FoldButton :label="t('header.extensions')" icon="pi pi-objects-column" severity="help" fold="xl" @click="$emit('extensions')" />
      <FoldButton :label="t('settings.title')" icon="pi pi-cog" severity="success" fold="xl" @click="$emit('settings')" />
      <Button
        v-if="auth.authenticated"
        :title="t('header.logout', { name: auth.username ?? '' })"
        :aria-label="t('header.logout', { name: auth.username ?? '' })"
        icon="pi pi-sign-out"
        severity="secondary"
        size="small"
        text
        @click="$emit('logout')"
      />
    </div>
  </header>
</template>
