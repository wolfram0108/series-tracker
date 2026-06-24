<script setup lang="ts">
import { onMounted, nextTick, ref, computed } from "vue"
import { api } from "../api/client"
import { useAuthStore } from "../stores/auth"

// Модалка первого запуска: создание администратора (имя, пароль, повтор).
// Показывается, когда админа ещё нет (GET /api/auth/status → configured=false).
// Стили — общие (styles/auth-modal.css).
const emit = defineEmits<{ (e: "done"): void }>()
const auth = useAuthStore()

const username = ref("")
const password = ref("")
const password2 = ref("")
const error = ref("")
const busy = ref(false)
const userInput = ref<HTMLInputElement | null>(null)

const tooShort = computed(
  () => password.value.length > 0 && password.value.length < 8)
const mismatch = computed(
  () => password2.value.length > 0 && password.value !== password2.value)
const canSubmit = computed(
  () => !!username.value.trim() && password.value.length >= 8 &&
    password.value === password2.value)

onMounted(async () => {
  await nextTick()
  userInput.value?.focus()
})

async function submit(): Promise<void> {
  if (busy.value || !canSubmit.value) return
  error.value = ""
  busy.value = true
  try {
    const { data, error: err, response } = await api.POST("/api/setup", {
      body: { username: username.value.trim(), password: password.value },
    } as never)
    const body = data as unknown as { success?: boolean; username?: string } | null
    if (response.ok && body?.success && body.username) {
      auth.setAuthenticated(body.username)
      emit("done")
      return
    }
    error.value =
      (err as unknown as { error?: string } | null)?.error ||
      "Не удалось создать администратора"
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
        <h2 class="login-title">Первый запуск</h2>
        <p class="login-sub">Создание администратора</p>

        <label class="login-field">
          <span>Имя администратора</span>
          <input
            ref="userInput"
            v-model="username"
            type="text"
            autocomplete="username"
            :disabled="busy"
          />
        </label>

        <label class="login-field">
          <span>Пароль (не короче 8 символов)</span>
          <input
            v-model="password"
            type="password"
            autocomplete="new-password"
            :disabled="busy"
          />
        </label>

        <label class="login-field">
          <span>Повтор пароля</span>
          <input
            v-model="password2"
            type="password"
            autocomplete="new-password"
            :disabled="busy"
          />
        </label>

        <p v-if="tooShort" class="login-error">Пароль не короче 8 символов.</p>
        <p v-else-if="mismatch" class="login-error">Пароли не совпадают.</p>
        <p v-else-if="error" class="login-error">{{ error }}</p>

        <button class="login-btn" type="submit" :disabled="busy || !canSubmit">
          {{ busy ? "Создание…" : "Создать и войти" }}
        </button>
      </form>
    </div>
  </Teleport>
</template>
