<script setup lang="ts">
import Button from "primevue/button"
import ModalShell from "./ModalShell.vue"
import { useConfirm } from "../composables/useConfirm"

// Глобальное окно подтверждения (монтируется один раз в App). Управляется
// singleton-состоянием useConfirm.
const { state, confirm, cancel } = useConfirm()
</script>

<template>
  <ModalShell v-if="state.visible" :title="state.title" @close="cancel">
    <!-- сообщение формируем мы (не пользовательский ввод) -->
    <p class="confirm-message" v-html="state.message"></p>
    <label v-if="state.checkbox" class="confirm-checkbox">
      <input type="checkbox" v-model="state.checkbox.checked" />
      <span>{{ state.checkbox.text }}</span>
    </label>
    <template #footer>
      <Button label="Отмена" icon="pi pi-times" severity="secondary" @click="cancel" />
      <Button label="Подтвердить" icon="pi pi-check" severity="danger" @click="confirm" />
    </template>
  </ModalShell>
</template>

<style scoped>
.confirm-message { margin: 0 0 4px; }
.confirm-checkbox { display: flex; align-items: center; gap: 8px; margin-top: 16px; cursor: pointer; }
.confirm-checkbox input { width: 18px; height: 18px; }
</style>
