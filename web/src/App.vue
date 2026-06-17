<script setup lang="ts">
import { ref } from "vue"
import InputText from "primevue/inputtext"
import InputNumber from "primevue/inputnumber"
import Password from "primevue/password"
import Select from "primevue/select"
import ToggleSwitch from "primevue/toggleswitch"
import Checkbox from "primevue/checkbox"
import SelectButton from "primevue/selectbutton"
import Button from "primevue/button"
import Tag from "primevue/tag"
import FloatLabel from "primevue/floatlabel"
import DataTable from "primevue/datatable"
import Column from "primevue/column"

// Парити-галерея (Ф2): PrimeVue с производным пресетом под токены
// series-tracker. Эталон для сравнения — старый фронт на «/».
const text = ref("Очень странные дела")
const floatText = ref("")
const num = ref(2)
const pwd = ref("")
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
]
</script>

<template>
  <main class="gallery">
    <header>
      <h1>Парити-галерея <small>/v2 · Ф2</small></h1>
      <p class="muted">
        PrimeVue с производным пресетом под токены series-tracker.
        Эталон для сравнения — старый фронт на «/». Цель — «близко».
      </p>
    </header>

    <section>
      <h2>Поля ввода</h2>
      <div class="grid">
        <div class="cell"><label>Текст</label><InputText v-model="text" /></div>
        <div class="cell"><label>Текст (invalid)</label><InputText v-model="text" invalid /></div>
        <div class="cell"><label>Текст (disabled)</label><InputText model-value="нельзя" disabled /></div>
        <div class="cell">
          <label>Floating label</label>
          <FloatLabel variant="on">
            <InputText id="fl" v-model="floatText" />
            <label for="fl">Путь сохранения</label>
          </FloatLabel>
        </div>
        <div class="cell"><label>Число</label><InputNumber v-model="num" show-buttons :min="0" :max="8" /></div>
        <div class="cell"><label>Пароль</label><Password v-model="pwd" toggle-mask :feedback="false" /></div>
        <div class="cell"><label>Select</label><Select v-model="sel" :options="selOptions" option-label="label" placeholder="Профиль правил" /></div>
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
        <Button label="Primary" icon="pi pi-check" />
        <Button label="Secondary" severity="secondary" />
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
      <h2>Теги / бейджи</h2>
      <div class="row">
        <Tag value="успех" severity="success" />
        <Tag value="ошибка" severity="danger" />
        <Tag value="ожидание" severity="warn" />
        <Tag value="инфо" severity="info" />
      </div>
    </section>

    <section>
      <h2>Таблица (DataTable)</h2>
      <DataTable :value="rows" size="small">
        <Column field="name" header="Сериал" />
        <Column field="status" header="Статус" />
        <Column field="progress" header="Прогресс, %" />
      </DataTable>
    </section>

    <section>
      <h2>Кастом-островок: срез карточки (на токенах)</h2>
      <p class="muted">Слой статуса карточки — кастом, но красится теми же токенами.</p>
      <div class="card-mock">
        <div class="card-bar"><span class="card-pill"><i class="pi pi-download" /> Загрузка</span></div>
        <div class="card-title">Фронт кровавой блокады</div>
      </div>
    </section>
  </main>
</template>

<style scoped>
.gallery { max-width: 960px; margin: 32px auto; padding: 0 20px 60px; }
h1 { margin-bottom: 4px; }
small { color: #888; font-weight: 400; font-size: 0.6em; }
.muted { color: var(--text-muted); }
section { margin-top: 28px; border-top: 1px solid var(--color-gray-200); padding-top: 16px; }
h2 { font-size: 1.1rem; margin-bottom: 12px; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 16px; }
.cell { display: flex; flex-direction: column; gap: 6px; }
.cell label { font-size: 0.8rem; color: var(--color-gray-600); }
.row { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; margin-bottom: 10px; }

/* срез карточки — кастом на токенах */
.card-mock {
  max-width: 360px; border-radius: var(--card-border-radius);
  box-shadow: var(--card-shadow); overflow: hidden; background: #fff;
}
.card-bar {
  height: 40px; display: flex; align-items: center; padding: 0 12px;
  background: linear-gradient(135deg, #009688, #00796D);
}
.card-pill {
  display: inline-flex; align-items: center; gap: 6px;
  background: rgba(255, 255, 255, 0.85); color: #00796D;
  border-radius: 20px; padding: 3px 10px; font-size: 0.85rem; font-weight: 600;
}
.card-title { padding: 12px; font-weight: 600; color: var(--color-text); }
</style>
