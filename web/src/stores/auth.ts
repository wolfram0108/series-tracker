import { defineStore } from "pinia"
import { ref } from "vue"

/** Состояние входа администратора (Этап 1, docs/security.md).
 *  showLogin поднимает перехватчик 401 (api/client middleware): любой
 *  запрос, отклонённый замком, открывает модалку входа. При выключенном
 *  замке 401 не приходит — модалка не появляется. */
export const useAuthStore = defineStore("auth", () => {
  const authenticated = ref(false)
  const username = ref("")
  const showLogin = ref(false)
  // первый запуск: администратор ещё не создан → модалка установки (имеет
  // приоритет над входом). Ставится по GET /api/auth/status при старте.
  const needsSetup = ref(false)

  function setAuthenticated(name: string): void {
    authenticated.value = true
    username.value = name
    showLogin.value = false
    needsSetup.value = false
  }

  function reset(): void {
    authenticated.value = false
    username.value = ""
  }

  return { authenticated, username, showLogin, needsSetup, setAuthenticated, reset }
})
