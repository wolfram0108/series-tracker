<script setup lang="ts">
import { onMounted, nextTick, ref } from "vue"
import { api } from "../api/client"
import { useAuthStore } from "../stores/auth"

// Модалка входа администратора (Этап 1). Блокирующая: закрыть нельзя, пока
// не вошёл (нет крестика, не закрывается по Esc/клику-вне). Сильно размывает
// задний фон (backdrop-filter), чтобы содержимое приложения было скрыто.
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

<style scoped>
/* Оверлей: сильное размытие + затемнение, поверх всего (включая модалки). */
.login-overlay {
  position: fixed;
  inset: 0;
  z-index: 1100;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  /* Нейтральное затемнение (как у прочих модалок проекта) + лёгкое
     размытие. ВРЕМЕННО: финальный вид (эффект матового стекла) выносится
     в отдельный ресёрч — docs/security.md, Этап 1. */
  background: rgba(15, 23, 42, 0.45);
  backdrop-filter: blur(6px);
  -webkit-backdrop-filter: blur(6px);
}

.login-card {
  width: 100%;
  max-width: 360px;
  background: var(--bg-white, #fff);
  border-radius: var(--card-border-radius, 12px);
  box-shadow: 0 16px 48px rgba(0, 0, 0, 0.28);
  padding: 32px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.login-title {
  margin: 0;
  font-size: 22px;
  font-weight: 700;
  color: #2c3e50;
  text-align: center;
}
.login-sub {
  margin: -10px 0 6px;
  text-align: center;
  color: var(--text-muted, #64748b);
  font-size: 13px;
  letter-spacing: 0.04em;
}

.login-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 13px;
  color: var(--text-muted, #64748b);
}
.login-field input {
  padding: 10px 12px;
  border: 1px solid var(--border-color, #e2e8f0);
  border-radius: 8px;
  font-size: 15px;
  color: #2c3e50;
  background: var(--bg-white, #fff);
  outline: none;
  transition: border-color 0.2s ease, box-shadow 0.2s ease;
}
.login-field input:focus {
  border-color: var(--color-blue, #3b82f6);
  box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.15);
}

.login-error {
  margin: 0;
  color: #dc2626;
  font-size: 13px;
  text-align: center;
}

.login-btn {
  margin-top: 8px;
  padding: 11px;
  border: none;
  border-radius: 8px;
  background: var(--color-blue, #3b82f6);
  color: #fff;
  font-size: 15px;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.2s ease, opacity 0.2s ease;
}
.login-btn:hover:not(:disabled) {
  filter: brightness(0.94);
}
.login-btn:disabled {
  opacity: 0.6;
  cursor: default;
}
</style>
