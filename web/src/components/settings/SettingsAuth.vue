<script setup lang="ts">
import { ref, onMounted } from "vue"
import StGroup from "../StGroup.vue"
import StIcon from "../StIcon.vue"
import StInput from "../StInput.vue"
import { api } from "../../api/client"
import { useApi } from "../../composables/useApi"

// Вкладка «Авторизация»: учётки сервисов (qBittorrent/Kinozal/VK/RuTracker/
// TMDB). GET /api/auth — загрузка, POST /api/auth — сохранение. Поля — через
// constructor-group (StGroup), пароли с глазиком (StInput type=password).
interface Creds {
  qbittorrent: { url: string; username: string; password: string }
  kinozal: { username: string; password: string }
  vk: { token: string }
  rutracker: { username: string; password: string }
  tmdb: { token: string }
}
const creds = ref<Creds>({
  qbittorrent: { url: "", username: "", password: "" },
  kinozal: { username: "", password: "" },
  vk: { token: "" },
  rutracker: { username: "", password: "" },
  tmdb: { token: "" },
})
// Задан ли секрет на бэке (сам секрет не приходит — Этап 3). Поле пароля
// остаётся пустым; пустое при сохранении = «не менять».
const configured = ref({
  qbittorrent: false, kinozal: false, vk: false, rutracker: false, tmdb: false,
})
const { request } = useApi()

/** Подпись поля секрета: помечает, что секрет уже задан. */
function secretLabel(base: string, on: boolean): string {
  return on ? `${base} (задан — пусто = без изменений)` : base
}

async function load() {
  const data = (await request(api.GET("/api/auth"))) as Record<string, never> | null
  if (!data) return
  const d = data as Record<
    string,
    { username?: string; url?: string; has_password?: boolean; configured?: boolean }
  >
  if (d.qbittorrent) {
    creds.value.qbittorrent.url = d.qbittorrent.url ?? ""
    creds.value.qbittorrent.username = d.qbittorrent.username ?? ""
    configured.value.qbittorrent = !!d.qbittorrent.has_password
  }
  if (d.kinozal) {
    creds.value.kinozal.username = d.kinozal.username ?? ""
    configured.value.kinozal = !!d.kinozal.has_password
  }
  if (d.rutracker) {
    creds.value.rutracker.username = d.rutracker.username ?? ""
    configured.value.rutracker = !!d.rutracker.has_password
  }
  if (d.vk) configured.value.vk = !!d.vk.has_password
  if (d.tmdb) configured.value.tmdb = !!d.tmdb.configured
}

async function save(): Promise<boolean> {
  const ok = await request(api.POST("/api/auth", { body: creds.value } as never), {
    errorMessage: "Ошибка сохранения настроек авторизации",
  })
  return ok !== null
}

onMounted(load)
defineExpose({ save })
</script>

<template>
  <div class="settings-auth">
    <div class="modern-fieldset">
      <div class="fieldset-header"><i class="pi pi-server"></i> qBittorrent</div>
      <div class="fieldset-content">
        <StGroup>
          <StIcon icon="pi pi-link" />
          <StInput v-model="creds.qbittorrent.url" label="URL" />
          <StIcon icon="pi pi-user" />
          <StInput v-model="creds.qbittorrent.username" label="Логин" />
          <StIcon icon="pi pi-lock" />
          <StInput v-model="creds.qbittorrent.password" :label="secretLabel('Пароль', configured.qbittorrent)" type="password" />
        </StGroup>
      </div>
    </div>

    <div class="modern-fieldset">
      <div class="fieldset-header"><i class="pi pi-download"></i> Kinozal.me</div>
      <div class="fieldset-content">
        <StGroup>
          <StIcon icon="pi pi-user" />
          <StInput v-model="creds.kinozal.username" label="Логин" />
          <StIcon icon="pi pi-lock" />
          <StInput v-model="creds.kinozal.password" :label="secretLabel('Пароль', configured.kinozal)" type="password" />
        </StGroup>
      </div>
    </div>

    <div class="modern-fieldset">
      <div class="fieldset-header"><i class="pi pi-youtube"></i> VK Video</div>
      <div class="fieldset-content">
        <StGroup>
          <StIcon icon="pi pi-key" />
          <StInput v-model="creds.vk.token" :label="secretLabel('Access Token', configured.vk)" type="password" />
        </StGroup>
      </div>
    </div>

    <div class="modern-fieldset">
      <div class="fieldset-header"><i class="pi pi-download"></i> RuTracker.org</div>
      <div class="fieldset-content">
        <StGroup>
          <StIcon icon="pi pi-user" />
          <StInput v-model="creds.rutracker.username" label="Логин" />
          <StIcon icon="pi pi-lock" />
          <StInput v-model="creds.rutracker.password" :label="secretLabel('Пароль', configured.rutracker)" type="password" />
        </StGroup>
      </div>
    </div>

    <div class="modern-fieldset">
      <div class="fieldset-header"><i class="pi pi-video"></i> The Movie Database (TMDB)</div>
      <div class="fieldset-content">
        <StGroup>
          <StIcon icon="pi pi-key" />
          <StInput v-model="creds.tmdb.token" :label="secretLabel('Read Access Token (v4)', configured.tmdb)" type="password" />
        </StGroup>
        <small class="fieldset-hint">Необходим для поиска информации о сериалах и эпизодах.</small>
      </div>
    </div>
  </div>
</template>
