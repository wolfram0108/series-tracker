<script setup lang="ts">
import Button from "primevue/button"
import { useIndicatorsStore } from "../stores/indicators"
import { useAuthStore } from "../stores/auth"

// Шапка приложения: заголовок + индикаторы агентов (реактивно из стора) +
// кнопки действий (открытие модалок — обрабатывает родитель).
const ind = useIndicatorsStore()
const auth = useAuthStore()
defineEmits<{ (e: "add"): void; (e: "logs"): void; (e: "settings"): void; (e: "logout"): void }>()
</script>

<template>
  <header class="page-header">
    <div class="page-title-container">
      <h1 class="page-title">WOLFRAM TS</h1>
      <div class="agent-indicators">
        <div class="indicator" :class="{ 'ind-active': ind.monitoring }" title="Агент мониторинга"></div>
        <div class="indicator ind-small" :class="{ 'ind-active': ind.downloader }" title="Агент загрузки (VK)"></div>
        <div class="indicator ind-small" :class="{ 'ind-active': ind.slicing }" title="Агент нарезки (VK)"></div>
      </div>
    </div>
    <div class="header-actions">
      <Button label="Добавить сериал" icon="pi pi-plus" @click="$emit('add')" />
      <Button label="Просмотр логов" icon="pi pi-book" severity="secondary" @click="$emit('logs')" />
      <Button label="Настройки" icon="pi pi-cog" severity="success" @click="$emit('settings')" />
      <Button
        v-if="auth.authenticated"
        :title="`Выйти (${auth.username})`"
        icon="pi pi-sign-out"
        severity="secondary"
        text
        @click="$emit('logout')"
      />
    </div>
  </header>
</template>
