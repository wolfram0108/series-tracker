<script setup lang="ts">
import { ref } from "vue"
import Button from "primevue/button"
import ModalShell from "./ModalShell.vue"
import SettingsAgents from "./settings/SettingsAgents.vue"

// Окно настроек (порт settingsModal): шапка с вкладками-сегментом + тело
// активной вкладки + футер. Вкладки auth/trackers/debug и конфигуратор
// (parser) — следующие под-вехи Ф4, пока заглушки.
const emit = defineEmits<{ (e: "close"): void }>()

const tab = ref<"auth" | "trackers" | "parser" | "agents" | "debug">("agents")
const tabs = [
  { label: "Авторизация", value: "auth", icon: "pi-key" },
  { label: "Трекеры", value: "trackers", icon: "pi-wifi" },
  { label: "Фильтры VK", value: "parser", icon: "pi-filter" },
  { label: "Агенты", value: "agents", icon: "pi-server" },
  { label: "Отладка", value: "debug", icon: "pi-wrench" },
] as const

function tabTitle(v: string): string {
  return tabs.find((t) => t.value === v)?.label ?? ""
}
</script>

<template>
  <ModalShell size="xl" @close="emit('close')">
    <template #title><i class="pi pi-cog"></i> Настройки</template>
    <template #header-extra>
      <div class="header-tabs st-tabs">
        <button
          v-for="t in tabs"
          :key="t.value"
          class="st-tab"
          :class="{ active: tab === t.value }"
          :title="t.label"
          @click="tab = t.value"
        >
          <i class="pi tab-icon" :class="t.icon"></i>
          <span class="tab-label" :data-text="t.label"><span class="tl-text">{{ t.label }}</span></span>
        </button>
      </div>
    </template>

    <SettingsAgents v-if="tab === 'agents'" />
    <div v-else-if="tab === 'parser'" class="tab-stub">
      <i class="pi pi-filter"></i>
      <p>Конфигуратор правил VK (drag-and-drop) — крупная отдельная веха Ф4, в работе.</p>
    </div>
    <div v-else class="tab-stub">
      <i class="pi pi-wrench"></i>
      <p>Вкладка «{{ tabTitle(tab) }}» — в следующих под-вехах Ф4.</p>
    </div>

    <template #footer>
      <Button label="Закрыть" icon="pi pi-times" severity="secondary" @click="emit('close')" />
    </template>
  </ModalShell>
</template>

<style scoped>
.tab-stub {
  display: flex; flex-direction: column; align-items: center; gap: 12px;
  padding: 48px 16px; color: var(--text-muted); text-align: center;
}
.tab-stub .pi { font-size: 2rem; opacity: 0.5; }
.tab-stub p { margin: 0; }
</style>
