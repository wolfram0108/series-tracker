<script setup lang="ts">
import { ref, computed } from "vue"
import Button from "primevue/button"
import ModalShell from "./ModalShell.vue"
import SettingsAgents from "./settings/SettingsAgents.vue"
import SettingsAuth from "./settings/SettingsAuth.vue"
import SettingsAccount from "./settings/SettingsAccount.vue"
import SettingsSources from "./settings/SettingsSources.vue"
import SettingsDebug from "./settings/SettingsDebug.vue"
import SettingsParser from "./settings/SettingsParser.vue"
import SettingsVoices from "./settings/SettingsVoices.vue"
import { t } from "../i18n"

// Окно настроек (порт settingsModal): шапка с вкладками-сегментом + тело
// активной вкладки + футер. Вкладка parser — конфигуратор «Фильтры VK».
const emit = defineEmits<{ (e: "close"): void }>()

const tab = ref<"auth" | "account" | "sources" | "parser" | "voices" | "agents" | "debug">("auth")

// ссылка на активную вкладку с методом save() (для кнопки в футере)
const tabRef = ref<{ save?: () => Promise<boolean> } | null>(null)
const savable = computed(() => tab.value === "auth")
const saving = ref(false)
async function onSave() {
  if (!tabRef.value?.save) return
  saving.value = true
  try {
    await tabRef.value.save()
  } finally {
    saving.value = false
  }
}
const tabs = [
  { label: "Авторизация", value: "auth", icon: "pi-key" },
  { label: "Аккаунт", value: "account", icon: "pi-user" },
  { label: t("settings.tab.sources"), value: "sources", icon: "pi-wifi" },
  { label: "Фильтры VK", value: "parser", icon: "pi-filter" },
  { label: t("settings.tab.voices"), value: "voices", icon: "pi-volume-up" },
  { label: "Агенты", value: "agents", icon: "pi-server" },
  { label: "Отладка", value: "debug", icon: "pi-wrench" },
] as const

</script>

<template>
  <ModalShell size="xl" fixed-height @close="emit('close')">
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

    <SettingsAuth v-if="tab === 'auth'" ref="tabRef" />
    <SettingsAccount v-else-if="tab === 'account'" />
    <SettingsSources v-else-if="tab === 'sources'" />
    <SettingsAgents v-else-if="tab === 'agents'" />
    <SettingsDebug v-else-if="tab === 'debug'" />
    <SettingsParser v-else-if="tab === 'parser'" />
    <SettingsVoices v-else-if="tab === 'voices'" />

    <template #footer>
      <Button
        v-if="savable"
        label="Сохранить"
        icon="pi pi-check"
        :loading="saving"
        @click="onSave"
      />
      <Button label="Закрыть" icon="pi pi-times" severity="secondary" @click="emit('close')" />
    </template>
  </ModalShell>
</template>
