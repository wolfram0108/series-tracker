<script setup lang="ts">
import { ref } from "vue"
import InputText from "primevue/inputtext"
import InputNumber from "primevue/inputnumber"
import Select from "primevue/select"
import ToggleSwitch from "primevue/toggleswitch"
import Checkbox from "primevue/checkbox"
import SelectButton from "primevue/selectbutton"
import Button from "primevue/button"
import Tag from "primevue/tag"
import DataTable from "primevue/datatable"
import Column from "primevue/column"
import StField from "./components/StField.vue"
import StGroup from "./components/StGroup.vue"
import StIcon from "./components/StIcon.vue"
import StInput from "./components/StInput.vue"
import StSelect from "./components/StSelect.vue"
import StBtn from "./components/StBtn.vue"

// Парити-галерея (Ф2). Эталон — старый фронт на «/».
const text = ref("Очень странные дела")
const num = ref(2)
const sel = ref(null)
const selOptions = [
  { label: "Профиль по умолчанию", value: 1 },
  { label: "Аниме", value: 2 },
]
const sw = ref(true)
const check = ref(true)
const mode = ref("search")
const modeOptions = [
  { label: "Быстрый поиск", value: "search" },
  { label: "Полное сканирование", value: "get_all" },
]
const rows = [
  { name: "Фронт кровавой блокады", status: "Загрузка", progress: 45 },
  { name: "Извне", status: "Готов", progress: 100 },
  { name: "Рик и Морти", status: "В очереди", progress: 0 },
]
const logs = [
  { time: "18.06.2026, 00:34:41", group: "gateway", level: "INFO", msg: "SSE клиент подключился (count=1)" },
  { time: "18.06.2026, 00:33:01", group: "https", level: "INFO", msg: "HTTP Request: GET /api/scanner/status \"HTTP/1.1 200 OK\"" },
  { time: "18.06.2026, 00:32:39", group: "torrents", level: "WARNING", msg: "серия 1: торрент отсутствует в qBit — запись сброшена" },
  { time: "18.06.2026, 00:31:10", group: "scan", level: "INFO", msg: "запущено модулей: 13" },
]

// StField (floating-label, порт constructor-group)
const fText = ref("")
const fPath = ref("/nas/media/Сериалы")
const fPwd = ref("секрет123")

// Нагруженные примеры из прода
const profileSel = ref<number | null>(null)
const profileOptions = [
  { label: "Аниме", value: 1 },
  { label: "Кино", value: 2 },
]
const newProfile = ref("")
const rtLogin = ref("bitter194")
const rtPass = ref("password123")
</script>

<template>
  <main class="gallery">
    <header>
      <h1>Парити-галерея <small>/v2 · Ф2</small></h1>
      <p class="muted">PrimeVue + StField (порт constructor-group) под токены series-tracker. Эталон — старый фронт на «/».</p>
    </header>

    <section>
      <h2>Поля — обычная высота (46px, PrimeVue)</h2>
      <div class="grid">
        <div class="cell"><label>Текст</label><InputText v-model="text" /></div>
        <div class="cell"><label>Текст (invalid)</label><InputText v-model="text" invalid /></div>
        <div class="cell"><label>Текст (disabled)</label><InputText model-value="нельзя" disabled /></div>
        <div class="cell"><label>Число</label><InputNumber v-model="num" show-buttons :min="0" :max="8" /></div>
        <div class="cell"><label>Select</label><Select v-model="sel" :options="selOptions" option-label="label" placeholder="Профиль правил" /></div>
      </div>
    </section>

    <section>
      <h2>Поля floating-label — повышенная высота (64px, StField)</h2>
      <div class="grid">
        <div class="cell"><StField v-model="fText" label="Название (RU)" /></div>
        <div class="cell"><StField v-model="fPath" label="Путь сохранения" icon="pi pi-folder-open" /></div>
        <div class="cell"><StField v-model="fPwd" label="Пароль" type="password" icon="pi pi-lock" /></div>
        <div class="cell"><StField model-value="" label="Имя (invalid)" icon="pi pi-tag" state="invalid" /></div>
      </div>
    </section>

    <section>
      <h2>Переключатели</h2>
      <div class="grid">
        <div class="cell"><label>ToggleSwitch</label><ToggleSwitch v-model="sw" /></div>
        <div class="cell"><label>Checkbox</label><Checkbox v-model="check" binary /></div>
        <div class="cell">
          <label>SelectButton (как btn-group)</label>
          <SelectButton v-model="mode" :options="modeOptions" option-label="label" option-value="value" />
        </div>
      </div>
    </section>

    <section>
      <h2>Кнопки</h2>
      <div class="row">
        <Button label="Сохранить" icon="pi pi-check" />
        <Button label="Закрыть" severity="secondary" icon="pi pi-times" />
        <Button label="Success" severity="success" icon="pi pi-save" />
        <Button label="Danger" severity="danger" icon="pi pi-trash" />
        <Button label="Warn" severity="warn" />
        <Button label="Info" severity="info" />
      </div>
      <div class="row">
        <Button label="Outlined" outlined />
        <Button label="Text" text />
        <Button label="Small" size="small" />
        <Button icon="pi pi-cog" rounded aria-label="cfg" />
      </div>
    </section>

    <section>
      <h2>Теги</h2>
      <div class="row">
        <Tag value="успех" severity="success" />
        <Tag value="ошибка" severity="danger" />
        <Tag value="ожидание" severity="warn" />
        <Tag value="инфо" severity="info" />
      </div>
    </section>

    <section>
      <h2>Таблицы (под div-table)</h2>
      <p class="muted small">Очередь — в fieldset с заголовком-баром (как у агентов); ниже — таблица логов.</p>
      <div class="fieldset" style="margin-bottom: 20px">
        <div class="fieldset-head"><span>Очередь Агента Загрузки (yt-dlp)</span></div>
        <div class="fieldset-body">
          <DataTable :value="rows" size="small">
            <Column field="name" header="Сериал / Файл" />
            <Column field="status" header="Статус" />
            <Column field="progress" header="Прогресс, %" />
          </DataTable>
        </div>
      </div>
      <DataTable :value="logs" size="small">
        <Column field="time" header="Время" />
        <Column field="group" header="Группа" />
        <Column field="level" header="Уровень" />
        <Column field="msg" header="Сообщение" />
      </DataTable>
    </section>

    <section class="loaded">
      <h2>Нагруженные примеры из прода (монолитные группы)</h2>
      <p class="muted small">Несколько айтемов соединены в один блок (одна рамка, внутренние разделители) — как в проде.</p>

      <div class="block">
        <p class="muted small">Выберите профиль для редактирования или создайте новый. Правила и тестирование появятся ниже после выбора профиля.</p>
        <StGroup>
          <StIcon icon="pi pi-user-edit" />
          <StSelect v-model="profileSel" :options="profileOptions" placeholder="-- Выберите профиль --" />
          <StInput v-model="newProfile" label="Имя нового профиля..." />
          <div class="constructor-item item-button-group">
            <StBtn icon="pi pi-plus" variant="add" title="Создать" :disabled="!newProfile" />
            <StBtn icon="pi pi-pencil" variant="edit" title="Переименовать" :disabled="!profileSel" />
            <StBtn icon="pi pi-trash" variant="delete" title="Удалить" :disabled="!profileSel" />
          </div>
        </StGroup>
      </div>

      <div class="fieldset">
        <div class="fieldset-head"><span>RuTracker.org</span></div>
        <div class="fieldset-body">
          <StGroup>
            <StIcon icon="pi pi-user" />
            <StInput v-model="rtLogin" label="Логин" />
            <StIcon icon="pi pi-lock" />
            <StInput v-model="rtPass" label="Пароль" type="password" />
          </StGroup>
        </div>
      </div>
    </section>
  </main>
</template>

<style scoped>
.gallery { max-width: 960px; margin: 32px auto; padding: 0 20px 60px; }
h1 { margin-bottom: 4px; }
small { color: #888; font-weight: 400; font-size: 0.6em; }
.muted { color: var(--text-muted); }
.small { font-size: 0.85rem; }
section { margin-top: 28px; border-top: 1px solid var(--color-gray-200); padding-top: 16px; }
h2 { font-size: 1.05rem; margin-bottom: 12px; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 16px; align-items: start; }
.cell { display: flex; flex-direction: column; gap: 6px; }
.cell > label { font-size: 0.8rem; color: var(--color-gray-600); }
.row { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; margin-bottom: 10px; }

/* нагруженные примеры */
.loaded .block { margin-bottom: 20px; }
.loaded .row { align-items: center; }
.loaded .row > .input-constructor-group { max-width: 280px; }
.fieldset {
  border: 1px solid var(--color-gray-200);
  border-radius: var(--card-border-radius);
  overflow: hidden;
}
.fieldset-head {
  background: var(--color-gray-100);
  padding: 8px 16px;
  text-align: right;
  font-weight: 600;
  color: var(--color-gray-600);
  border-bottom: 1px solid var(--color-gray-200);
}
.fieldset-body { padding: 16px; }
.fieldset-body.two { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
</style>
