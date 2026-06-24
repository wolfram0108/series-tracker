<script setup lang="ts">
import { ref, computed, watch } from "vue"
import Button from "primevue/button"
import StGroup from "./StGroup.vue"
import StIcon from "./StIcon.vue"
import StInput from "./StInput.vue"
import { api } from "../api/client"
import { useAuthStore } from "../stores/auth"

// Модалка входа администратора (Этап 1). Блокирующая. Валидация — рамкой
// StGroup; причина/ошибка — плашкой ПОД карточкой (login-notice).
const emit = defineEmits<{ (e: "logged-in"): void }>()
const auth = useAuthStore()

const username = ref("")
const password = ref("")
const busy = ref(false)
const attempted = ref(false)
const notice = ref("")

type VState = "valid" | "invalid" | null
const userState = computed<VState>(
  () => (attempted.value ? (username.value ? "valid" : "invalid") : null))
const passState = computed<VState>(
  () => (attempted.value ? (password.value ? "valid" : "invalid") : null))

watch([username, password], () => { notice.value = "" })

async function submit(): Promise<void> {
  attempted.value = true
  if (busy.value) return
  if (!username.value || !password.value) {
    notice.value = "Введите логин и пароль"
    return
  }
  busy.value = true
  try {
    const { data, error, response } = await api.POST("/api/login", {
      body: { username: username.value, password: password.value },
    } as never)
    const body = data as unknown as { success?: boolean; username?: string } | null
    if (response.ok && body?.success && body.username) {
      auth.setAuthenticated(body.username)
      emit("logged-in")
      return
    }
    notice.value = (error as unknown as { error?: string } | null)?.error ||
      "Неверный логин или пароль"
  } catch {
    notice.value = "Ошибка сети — сервер недоступен"
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <Teleport to="body">
    <div class="login-overlay">
      <form class="login-card" @submit.prevent="submit">
        <h2 class="login-title">Вход</h2>
        <p class="login-sub">WOLFRAM TS</p>

        <StGroup :state="userState">
          <StIcon icon="pi pi-user" />
          <StInput v-model="username" label="Логин" />
        </StGroup>

        <StGroup :state="passState">
          <StIcon icon="pi pi-lock" />
          <StInput v-model="password" label="Пароль" type="password" />
        </StGroup>

        <Button class="login-submit" type="submit" label="Войти"
                icon="pi pi-sign-in" :loading="busy" />
      </form>

      <Transition name="notice">
        <div v-if="notice" class="login-notice">{{ notice }}</div>
      </Transition>
    </div>
  </Teleport>
</template>
