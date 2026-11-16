<script setup lang="ts">
import Button from "primevue/button"
import ModalShell from "./ModalShell.vue"
import { useConfirm } from "../composables/useConfirm"
import { t } from "../i18n"

// Глобальное окно подтверждения (монтируется один раз в App). Управляется
// singleton-состоянием useConfirm.
const { state, confirm, cancel } = useConfirm()
</script>

<template>
  <ModalShell v-if="state.visible" :title="state.title" @close="cancel">
    <!-- Plain text: a message carries names the user typed (a series, a table), never markup. -->
    <p class="confirm-message">{{ state.message }}</p>
    <template #footer>
      <Button :label="t('common.cancel')" icon="pi pi-times" severity="secondary" @click="cancel" />
      <Button :label="t('common.confirm')" icon="pi pi-check" severity="danger" @click="confirm" />
    </template>
  </ModalShell>
</template>

<style scoped>
.confirm-message { margin: 0 0 4px; }
</style>
