<script setup lang="ts">
import { ref, onMounted } from "vue"
import SheetField from "../SheetField.vue"
import SheetRow from "../SheetRow.vue"
import StInput from "../StInput.vue"
import { api } from "../../api/client"
import { useApi } from "../../composables/useApi"
import { t } from "../../i18n"
import LoadGate from "../LoadGate.vue"
import { useLoad } from "../../composables/useLoad"

// Вкладка «Авторизация»: учётки сервисов (qBittorrent/Kinozal/VK/RuTracker/
// TMDB). GET /api/auth — загрузка, POST /api/auth — сохранение. Поля — через
// листа (SheetRow/SheetField), пароли с глазиком (StInput type=password).
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

// The eye of a field of a secret asks the core for it (GET /api/auth/{service}/secret): the secrets are not sent with the rest.
async function reveal(service: string, held: boolean): Promise<string | null> {
  if (!held) return null
  const got = (await request(api.GET("/api/auth/{service}/secret", { params: { path: { service } } }))) as { secret?: string } | null
  return got?.secret || null
}

async function load(): Promise<boolean> {
  const data = (await request(api.GET("/api/auth"))) as Record<string, never> | null
  if (!data) return false
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
  return true
}
const gate = useLoad(load)

async function save(): Promise<boolean> {
  if (!gate.loaded.value) return false // what was never read is not written over what the core holds
  const ok = await request(api.POST("/api/auth", { body: creds.value }), {
    errorMessage: t("auth.saveFailed"),
  })
  return ok !== null
}

onMounted(gate.run)
defineExpose({ save, ready: gate.loaded })
</script>

<template>
  <LoadGate :state="gate.state.value" :rows="5" @retry="gate.run">
    <div class="sheet settings-auth">
      <SheetRow caption="qBittorrent">
        <div class="sheet-pair">
          <SheetField label="URL" icon="pi pi-link">
            <StInput v-model="creds.qbittorrent.url" />
          </SheetField>
          <SheetField :label="t('auth.login')" icon="pi pi-user">
            <StInput v-model="creds.qbittorrent.username" />
          </SheetField>
          <SheetField :label="t('auth.password')" icon="pi pi-lock">
            <StInput v-model="creds.qbittorrent.password" type="password" :placeholder="configured.qbittorrent ? t('auth.secret.mask') : undefined" :reveal="() => reveal('qbittorrent', configured.qbittorrent)" />
          </SheetField>
        </div>
      </SheetRow>

      <SheetRow caption="Kinozal.me">
        <div class="sheet-pair">
          <SheetField :label="t('auth.login')" icon="pi pi-user">
            <StInput v-model="creds.kinozal.username" />
          </SheetField>
          <SheetField :label="t('auth.password')" icon="pi pi-lock">
            <StInput v-model="creds.kinozal.password" type="password" :placeholder="configured.kinozal ? t('auth.secret.mask') : undefined" :reveal="() => reveal('kinozal', configured.kinozal)" />
          </SheetField>
        </div>
      </SheetRow>

      <SheetRow caption="VK Video">
        <div class="sheet-pair">
          <SheetField :label="'Access Token'" icon="pi pi-key">
            <StInput v-model="creds.vk.token" type="password" :placeholder="configured.vk ? t('auth.secret.mask') : undefined" :reveal="() => reveal('vk', configured.vk)" />
          </SheetField>
        </div>
      </SheetRow>

      <SheetRow caption="RuTracker.org">
        <div class="sheet-pair">
          <SheetField :label="t('auth.login')" icon="pi pi-user">
            <StInput v-model="creds.rutracker.username" />
          </SheetField>
          <SheetField :label="t('auth.password')" icon="pi pi-lock">
            <StInput v-model="creds.rutracker.password" type="password" :placeholder="configured.rutracker ? t('auth.secret.mask') : undefined" :reveal="() => reveal('rutracker', configured.rutracker)" />
          </SheetField>
        </div>
      </SheetRow>

      <SheetRow caption="The Movie Database (TMDB)">
        <div class="sheet-pair">
          <SheetField :label="'Read Access Token (v4)'" icon="pi pi-key">
            <StInput v-model="creds.tmdb.token" type="password" :placeholder="configured.tmdb ? t('auth.secret.mask') : undefined" :reveal="() => reveal('tmdb', configured.tmdb)" />
          </SheetField>
        </div>
        <p class="sheet-hint">{{ t("auth.tmdbHint") }}</p>
      </SheetRow>
    </div>
  </LoadGate>
</template>
