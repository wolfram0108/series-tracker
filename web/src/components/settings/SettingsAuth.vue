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
const { request } = useApi()

async function load() {
  const data = (await request(api.GET("/api/auth"))) as Record<string, never> | null
  if (!data) return
  const d = data as Record<string, Record<string, string>>
  if (d.qbittorrent) Object.assign(creds.value.qbittorrent, d.qbittorrent)
  if (d.kinozal) Object.assign(creds.value.kinozal, d.kinozal)
  if (d.vk) creds.value.vk.token = d.vk.password ?? "" // легаси: vk-токен хранится в поле password
  if (d.rutracker) Object.assign(creds.value.rutracker, d.rutracker)
  if (d.tmdb) creds.value.tmdb.token = d.tmdb.token ?? ""
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
          <StInput v-model="creds.qbittorrent.password" label="Пароль" type="password" />
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
          <StInput v-model="creds.kinozal.password" label="Пароль" type="password" />
        </StGroup>
      </div>
    </div>

    <div class="modern-fieldset">
      <div class="fieldset-header"><i class="pi pi-youtube"></i> VK Video</div>
      <div class="fieldset-content">
        <StGroup>
          <StIcon icon="pi pi-key" />
          <StInput v-model="creds.vk.token" label="Access Token" type="password" />
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
          <StInput v-model="creds.rutracker.password" label="Пароль" type="password" />
        </StGroup>
      </div>
    </div>

    <div class="modern-fieldset">
      <div class="fieldset-header"><i class="pi pi-video"></i> The Movie Database (TMDB)</div>
      <div class="fieldset-content">
        <StGroup>
          <StIcon icon="pi pi-key" />
          <StInput v-model="creds.tmdb.token" label="Read Access Token (v4)" type="password" />
        </StGroup>
        <small class="fieldset-hint">Необходим для поиска информации о сериалах и эпизодах.</small>
      </div>
    </div>
  </div>
</template>
