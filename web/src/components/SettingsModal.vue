<script setup lang="ts">
import { ref, computed, watch } from "vue"
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

// The rules are built when their tab is first shown and kept to the close of the window: what was typed in the test stays
// when another tab is open (the same section of the window of a series).
const parserBuilt = ref(false)
watch(tab, (next) => {
  if (next === "parser") parserBuilt.value = true
})

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
  { label: t("settings.tab.auth"), value: "auth", icon: "pi-key" },
  { label: t("settings.tab.account"), value: "account", icon: "pi-user" },
  { label: t("settings.tab.sources"), value: "sources", icon: "pi-wifi" },
  { label: t("rules.tab"), value: "parser", icon: "pi-filter" },
  { label: t("settings.tab.voices"), value: "voices", icon: "pi-volume-up" },
  { label: t("settings.tab.agents"), value: "agents", icon: "pi-server" },
  { label: t("settings.tab.debug"), value: "debug", icon: "pi-wrench" },
] as const

</script>

<template>
  <ModalShell size="xl" fixed-height @close="emit('close')">
    <template #title><i class="pi pi-cog"></i> {{ t("settings.title") }}</template>
    <template #header-extra>
      <div class="header-tabs st-tabs">
        <button
          v-for="tb in tabs"
          :key="tb.value"
          class="st-tab"
          :class="{ active: tab === tb.value }"
          :title="tb.label"
          @click="tab = tb.value"
        >
          <i class="pi tab-icon" :class="tb.icon"></i>
          <span class="tab-label" :data-text="tb.label"><span class="tl-text">{{ tb.label }}</span></span>
        </button>
      </div>
    </template>

    <SettingsAuth v-if="tab === 'auth'" ref="tabRef" />
    <SettingsAccount v-else-if="tab === 'account'" />
    <SettingsSources v-else-if="tab === 'sources'" />
    <SettingsAgents v-else-if="tab === 'agents'" />
    <SettingsDebug v-else-if="tab === 'debug'" />
    <SettingsVoices v-else-if="tab === 'voices'" />
    <SettingsParser v-if="parserBuilt" v-show="tab === 'parser'" />

    <template #footer>
      <Button
        v-if="savable"
        :label="t('common.save')"
        icon="pi pi-check"
        :loading="saving"
        @click="onSave"
      />
      <Button :label="t('common.close')" icon="pi pi-times" severity="secondary" @click="emit('close')" />
    </template>
  </ModalShell>
</template>
