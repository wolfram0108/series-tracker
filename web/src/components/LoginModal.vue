<script setup lang="ts">
import { onMounted, nextTick, ref } from "vue"
import { api } from "../api/client"
import { useAuthStore } from "../stores/auth"

// Модалка входа администратора (Этап 1). Блокирующая: закрыть нельзя, пока
// не вошёл (нет крестика, не закрывается по Esc/клику-вне). Стили — общие
// (styles/auth-modal.css), оформление черновое (см. docs/security.md).
const emit = defineEmits<{ (e: "logged-in"): void }>()
const auth = useAuthStore()

const username = ref("")
const password = ref("")
const error = ref("")
const busy = ref(false)
const userInput = ref<HTMLInputElement | null>(null)

onMounted(async () => {
  await nextTick()
  userInput.value?.focus()
})

async function submit(): Promise<void> {
  if (busy.value) return
  error.value = ""
  busy.value = true
  try {
    const { data, error: err, response } = await api.POST("/api/login", {
      body: { username: username.value, password: password.value },
    } as never)
    const body = data as unknown as { success?: boolean; username?: string } | null
    if (response.ok && body?.success && body.username) {
      auth.setAuthenticated(body.username)
      emit("logged-in")
      return
    }
    error.value =
      (err as unknown as { error?: string } | null)?.error || "Не удалось войти"
  } catch {
    error.value = "Ошибка сети — сервер недоступен"
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

        <label class="login-field">
          <span>Логин</span>
          <input
            ref="userInput"
            v-model="username"
            type="text"
            autocomplete="username"
            :disabled="busy"
          />
        </label>

        <label class="login-field">
          <span>Пароль</span>
          <input
            v-model="password"
            type="password"
            autocomplete="current-password"
            :disabled="busy"
          />
        </label>

        <p v-if="error" class="login-error">{{ error }}</p>

        <button class="login-btn" type="submit" :disabled="busy">
          {{ busy ? "Вход…" : "Войти" }}
        </button>
      </form>
    </div>
  </Teleport>
</template>
