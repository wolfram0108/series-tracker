<script setup lang="ts">
import { ref, computed } from "vue"
import Button from "primevue/button"
import { useToast } from "primevue/usetoast"
import StGroup from "../StGroup.vue"
import StIcon from "../StIcon.vue"
import StInput from "../StInput.vue"
import { api } from "../../api/client"
import { useApi } from "../../composables/useApi"
import { useAuthStore } from "../../stores/auth"
import { t, tAround } from "../../i18n"

// Вкладка «Аккаунт»: смена пароля администратора. POST /api/auth/password
// (за «замком»): обновляет хэш. Текущий пароль не запрашивается — активная
// сессия уже подтверждает владельца (решение для однопользовательского
// приложения). Поля — StGroup с валидацией рамкой; результат — Toast.
const auth = useAuthStore()
const toast = useToast()
const { request, loading } = useApi()

const next = ref("")
const next2 = ref("")
const attempted = ref(false)

type VState = "valid" | "invalid" | null
const touched = (v: string) => attempted.value || v.length > 0

const nextState = computed<VState>(
  () => (touched(next.value) ? (next.value.length >= 8 ? "valid" : "invalid") : null))
const next2State = computed<VState>(() => {
  if (!touched(next2.value)) return null
  return next2.value === next.value && next.value.length >= 8 ? "valid" : "invalid"
})
const hint = tAround("account.hint", "name")

const canSubmit = computed(
  () => next.value.length >= 8 && next.value === next2.value)

async function submit(): Promise<void> {
  attempted.value = true
  if (!canSubmit.value) {
    toast.add({
      severity: "warn", summary: t("common.checkFields"), life: 4000,
      detail: next.value.length < 8
        ? t("account.error.short")
        : t("common.passwordsMismatch"),
    })
    return
  }
  const ok = await request(
    api.POST("/api/auth/password", {
      body: { new: next.value },
    } as never),
    { errorMessage: t("account.error.change") },
  )
  if (ok === null) return
  toast.add({ severity: "success", summary: t("common.done"),
    detail: t("account.changed"), life: 4000 })
  next.value = ""
  next2.value = ""
  attempted.value = false
}
</script>

<template>
  <div class="settings-account">
    <div class="modern-fieldset">
      <div class="fieldset-header"><i class="pi pi-lock"></i> {{ t("account.section") }}</div>
      <div class="fieldset-content">
        <StGroup :state="nextState">
          <StIcon icon="pi pi-key" />
          <StInput v-model="next" :label="t('account.new')" type="password" />
        </StGroup>
        <StGroup :state="next2State">
          <StIcon icon="pi pi-key" />
          <StInput v-model="next2" :label="t('account.repeat')" type="password" />
        </StGroup>
        <small class="fieldset-hint">{{ hint[0] }}<b>{{ auth.adminName }}</b>{{ hint[1] }}</small>
        <div class="account-actions">
          <Button :label="t('account.change')" icon="pi pi-check"
                  :loading="loading" @click="submit" />
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* Вертикальные зазоры между группами полей (по умолчанию fieldset-content
   их не задаёт — в других вкладках поля лежат одной группой). */
.settings-account :deep(.fieldset-content) {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.settings-account :deep(.fieldset-hint) {
  margin-top: 0;
}
.account-actions {
  display: flex;
  justify-content: flex-end;
}
</style>
