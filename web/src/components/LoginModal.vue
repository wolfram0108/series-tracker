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

// Модалка входа администратора (Этап 1). Блокирующая. Приложение
// однопользовательское — логин не вводят, показываем имя уже созданного
// администратора (auth.adminName), поле остаётся одно — пароль. Валидация —
// рамкой StGroup; причина/ошибка — плашкой ПОД карточкой (login-notice).
const emit = defineEmits<{ (e: "logged-in"): void }>()
const auth = useAuthStore()

const password = ref("")
const busy = ref(false)
const attempted = ref(false)
const notice = ref("")

useBodyScrollLock()  // фон не прокручивается, пока окно открыто

type VState = "valid" | "invalid" | null
const passState = computed<VState>(
  () => (attempted.value ? (password.value ? "valid" : "invalid") : null))

watch(password, () => { notice.value = "" })

async function submit(): Promise<void> {
  attempted.value = true
  if (busy.value) return
  if (!password.value) {
    notice.value = t("login.error.empty")
    return
  }
  busy.value = true
  try {
    const { data, error, response } = await api.POST("/api/login", {
      body: { username: auth.adminName, password: password.value },
    } as never)
    const body = data as unknown as { success?: boolean; username?: string } | null
    if (response.ok && body?.success && body.username) {
      auth.setAuthenticated(body.username)
      emit("logged-in")
      return
    }
    notice.value = (error as unknown as { error?: string } | null)?.error ||
      t("login.error.wrong")
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
        <h2 class="login-title">{{ t("login.title") }}</h2>
        <p class="login-user">{{ auth.adminName }}</p>

        <StGroup :state="passState">
          <StIcon icon="pi pi-lock" />
          <StInput v-model="password" :label="t('auth.password')" type="password" />
        </StGroup>

        <Button class="login-submit" type="submit" :label="t('login.submit')"
                icon="pi pi-sign-in" :loading="busy" />
      </form>

      <Transition name="notice">
        <div v-if="notice" class="login-notice">{{ notice }}</div>
      </Transition>
    </div>
  </Teleport>
</template>
