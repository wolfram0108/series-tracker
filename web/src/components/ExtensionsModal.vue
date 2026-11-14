<script setup lang="ts">
import { ref } from "vue"
import Button from "primevue/button"
import ModalShell from "./ModalShell.vue"
import ModalTabs from "./ModalTabs.vue"
import ExtensionChines from "./extensions/ExtensionChines.vue"
import ExtensionConform from "./extensions/ExtensionConform.vue"
import { t } from "../i18n"

// The window of the extensions: what the tracker takes from outside as modules of their own — each a tab with the address it is
// reached at, its state and its parameters. A module comes here as a tab that holds a sheet (components/SheetRow.vue).
const emit = defineEmits<{ (e: "close"): void }>()

const tab = ref<"chines" | "conform">("chines")
const tabs = [
  { label: t("extensions.tab.chines"), value: "chines", icon: "pi-server" },
  { label: t("extensions.tab.conform"), value: "conform", icon: "pi-sliders-h" },
] as const
</script>

<template>
  <ModalShell size="xl" fixed-height @close="emit('close')">
    <template #title><i class="pi pi-objects-column"></i> {{ t("extensions.title") }}</template>
    <template #header-extra>
      <ModalTabs v-model="tab" :tabs="tabs" />
    </template>

    <ExtensionChines v-if="tab === 'chines'" />
    <ExtensionConform v-else />

    <template #footer>
      <Button :label="t('common.close')" icon="pi pi-times" severity="secondary" @click="emit('close')" />
    </template>
  </ModalShell>
</template>
