<script setup lang="ts">
import { ref, computed, watch } from "vue"
import Button from "primevue/button"
import SheetField from "./SheetField.vue"
import StInput from "./StInput.vue"
import { api } from "../api/client"
import { t } from "../i18n"
import { useAuthStore } from "../stores/auth"
import { useBodyScrollLock } from "../composables/useBodyScrollLock"
import { reasonText } from "../lib/reason"

// Модалка первого запуска: создание администратора (имя, пароль, повтор).
// Валидация — рамкой StGroup; причина/ошибка — плашкой ПОД карточкой
// (login-notice), окно не «прыгает».
const emit = defineEmits<{ (e: "done"): void }>()
const auth = useAuthStore()

const username = ref("")
const password = ref("")
const password2 = ref("")
const busy = ref(false)
const attempted = ref(false)
const notice = ref("")

useBodyScrollLock()  // фон не прокручивается, пока окно открыто

type VState = "valid" | "invalid" | null
const touched = (v: string) => attempted.value || v.length > 0

const userState = computed<VState>(
  () => (touched(username.value) ? (username.value.trim() ? "valid" : "invalid") : null))
const passState = computed<VState>(
  () => (touched(password.value) ? (password.value.length >= 8 ? "valid" : "invalid") : null))
const pass2State = computed<VState>(() => {
  if (!touched(password2.value)) return null
  return password2.value === password.value && password.value.length >= 8
    ? "valid" : "invalid"
})
const canSubmit = computed(
  () => !!username.value.trim() && password.value.length >= 8 &&
    password.value === password2.value)

// правка любого поля убирает плашку
watch([username, password, password2], () => { notice.value = "" })

async function submit(): Promise<void> {
  attempted.value = true
  if (busy.value) return
  if (!canSubmit.value) {
    notice.value = !username.value.trim()
      ? t("setup.error.username")
      : password.value.length < 8
        ? t("setup.error.passwordShort")
        : t("common.passwordsMismatch")
    return
  }
  busy.value = true
  try {
    const { data, error, response } = await api.POST("/api/setup", {
      body: { username: username.value.trim(), password: password.value },
    } as never)
    const body = data as unknown as { success?: boolean; username?: string } | null
    if (response.ok && body?.success && body.username) {
      auth.setAuthenticated(body.username)
      emit("done")
      return
    }
    notice.value = reasonText((error as unknown as { error?: string } | null)?.error) ||
      t("setup.error.create")
  } catch {
    notice.value = t("common.networkDown")
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <Teleport to="body">
    <div class="login-overlay">
      <form class="login-card" @submit.prevent="submit">
        <h2 class="login-title">{{ t("setup.title") }}</h2>
        <p class="login-sub">{{ t("setup.sub") }}</p>

        <SheetField :label="t('setup.username')" icon="pi pi-user" :state="userState">
          <StInput v-model="username" />
        </SheetField>

        <SheetField :label="t('setup.password')" icon="pi pi-lock" :state="passState">
          <StInput v-model="password" type="password" :placeholder="t('setup.passwordHint')" />
        </SheetField>

        <SheetField :label="t('setup.passwordRepeat')" icon="pi pi-lock" :state="pass2State">
          <StInput v-model="password2" type="password" />
        </SheetField>

        <Button class="login-submit" type="submit" :label="t('setup.submit')"
                icon="pi pi-check" :loading="busy" />
      </form>

      <Transition name="notice">
        <div v-if="notice" class="login-notice">{{ notice }}</div>
      </Transition>
    </div>
  </Teleport>
</template>
