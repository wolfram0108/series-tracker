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
  // имя администратора для экрана входа: приложение однопользовательское,
  // поле ввода логина не нужно — показываем уже созданного пользователя.
  // Ставится по GET /api/auth/status при старте (поле admin).
  const adminName = ref("")
  // первый запуск: администратор ещё не создан → модалка установки (имеет
  // приоритет над входом). Ставится по GET /api/auth/status при старте.
  const needsSetup = ref(false)

  function setAuthenticated(name: string): void {
    authenticated.value = true
    username.value = name
    adminName.value = name
    showLogin.value = false
    needsSetup.value = false
  }

  function reset(): void {
    authenticated.value = false
    username.value = ""
  }

  return { authenticated, username, adminName, showLogin, needsSetup,
    setAuthenticated, reset }
})
