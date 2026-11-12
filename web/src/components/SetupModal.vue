<script setup lang="ts">
import { ref, computed, watch } from "vue"
import Button from "primevue/button"
import StGroup from "./StGroup.vue"
import StIcon from "./StIcon.vue"
import StInput from "./StInput.vue"
import { api } from "../api/client"
import { t } from "../i18n"
import { useAuthStore } from "../stores/auth"
import { useBodyScrollLock } from "../composables/useBodyScrollLock"

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
    notice.value = (error as unknown as { error?: string } | null)?.error ||
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

        <StGroup :state="userState">
          <StIcon icon="pi pi-user" />
          <StInput v-model="username" :label="t('setup.username')" />
        </StGroup>

        <StGroup :state="passState">
          <StIcon icon="pi pi-lock" />
          <StInput v-model="password" :label="t('setup.password')" type="password" />
        </StGroup>

        <StGroup :state="pass2State">
          <StIcon icon="pi pi-lock" />
          <StInput v-model="password2" :label="t('setup.passwordRepeat')" type="password" />
        </StGroup>

        <Button class="login-submit" type="submit" :label="t('setup.submit')"
                icon="pi pi-check" :loading="busy" />
      </form>

      <Transition name="notice">
        <div v-if="notice" class="login-notice">{{ notice }}</div>
      </Transition>
    </div>
  </Teleport>
</template>
