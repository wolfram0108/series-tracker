# Прогресс переписывания фронта — точка продолжения (handoff)

> Живой снимок состояния для бесшовного продолжения после сжатия
> контекста. План/ТЗ — [docs/frontend-rewrite.md](frontend-rewrite.md)
> (решения Р-Ф1..Р-Ф12, фазы, §13 под-вехи Ф4). Этот файл — что уже
> сделано и что дальше. Обновлено: 2026-06-18 (после блока карточек).

---

## 1. Где мы в плане

| Фаза | Статус |
|---|---|
| **Ф0** — типизация бэка (response_model на 62 + OpenAPI + golden) | ✅ **завершена** |
| **Ф1** — каркас `web/` (Vite+Vue3+TS+PrimeVue+Pinia) под `/v2` | ✅ **завершена** |
| **Ф2** — токен-пресет + парити-галерея | 🔄 **почти закрыта** (см. §4) — осталось табы + модалка |
| Ф3 — API-слой (типы+useSSE+сторы) | ⏳ не начата |
| Ф4 — перенос экранов (под-вехи §13 ТЗ) | ⏳ (дизайн части экранов уже сделан в галерее — см. ниже) |
| Ф5 — приёмка паритета | ⏳ |
| Ф6 — cutover `/v2`→`/` | ⏳ |

**Важно про границу Ф2/Ф4:** галерея переросла «парити примитивов» — в ней
уже спроектированы реальные **составные экраны** (карточки-сущности всех
мест, очереди Агентов вместо таблиц). Это де-факто визуальная часть Ф4,
сделанная заранее на моках. Когда дойдём до Ф4 «по-настоящему» — переносим
эти готовые карточные раскладки в реальные компоненты на живых данных.

Стратегия (Р-Ф7): **полная переделка целиком в `/v2`**, старый фронт на
`/`, переключение по полному паритету. Без срезания углов (память:
`feedback-full-rewrites-no-corner-cutting`).

---

## 2. Операционка (как собирать, смотреть, проверять)

**Стенд:** я работаю как **root**; приложение под systemd от `user`.
**Node 20** на стенде. Адрес снаружи: `http://192.168.1.148:5000`
(старый фронт `/`, новый `/v2`).

```bash
# Сборка нового фронта (после правок web/):
cd /home/user/series-tracker/web && npm run build      # → web/dist (vue-tsc strict!)
chown -R user:user /home/user/series-tracker/web        # root создаёт файлы — вернуть владельца
# /v2 отдаётся сразу (gateway монтирует web/dist, рестарт НЕ нужен для статики)

# Дев-режим: cd web && npm run dev   (Vite proxy /api → :5000)
# Регенерация TS-типов из OpenAPI:  cd web && npm run gen:api  → src/api/schema.d.ts
# Перезапуск бэка (после Python):   sudo systemctl restart series-tracker

# Скриншоты (Playwright+firefox, в т.ч. /root/.cache):
.venv/bin/python - <<'PY'
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.firefox.launch()
    pg = b.new_page(viewport={"width":900,"height":900}, device_scale_factor=2)
    pg.goto("http://127.0.0.1:5000/v2/", wait_until="networkidle")   # новый — networkidle ок
    # старый фронт /: wait_until="domcontentloaded" (+wait_for_timeout) — вечный SSE!
    pg.screenshot(path="/tmp/v2.png", full_page=True)
    b.close()
PY
# затем смотреть PNG инструментом Read. Для секции: найти h2/h3 по тексту,
# el.scroll_into_view_if_needed(); снимать el или el.nextElementSibling.

# Golden-харнесс контракта (страховка additive-правок бэка, Ф0):
.venv/bin/python tests/api_golden.py capture   # эталон ДО
.venv/bin/python tests/api_golden.py check     # сверка ПОСЛЕ
```

**Дисциплина:** русский; без AI-атрибуции; коммит на каждый согласованный
шаг; vue-tsc strict при build (следить за неиспользуемыми импортами).
**Архитектурные/UI-правки — только после явного «да» пользователя.**

---

## 3. Что построено в `web/` (стек и файлы)

Стек (выверенные версии): Vite 6, Vue 3.5 SFC+TS 5.7, vue-tsc 2,
PrimeVue 4.2 (Aura), Pinia 2, openapi-fetch/typescript, vuedraggable 4.

| Файл | Назначение |
|---|---|
| `src/main.ts` | bootstrap: Pinia + PrimeVue(STPreset, `darkModeSelector: ".st-dark"`) + импорт стилей (tokens/style/fields/overrides/tables/pills/card/cards/progress) |
| `src/theme/preset.ts` | `STPreset` = definePreset(Aura): primary = Bootstrap-синий #0d6efd, радиусы 9px |
| `src/styles/tokens.css` | порт `variables.css`; **+ `--font-mono`, `--card-animation-duration: 0.3s`** (были потеряны) |
| `src/styles/overrides.css` | кнопки-градиенты, поля PrimeVue (46px/бордер 2px/фокус/invalid) |
| `src/styles/fields.css` | порт constructor-group (floating-label 64px, item-select, btn-icon) |
| `src/styles/tables.css` | DataTable под `div-table` |
| `src/styles/pills.css` | бейдж очереди (`.badge bg-*` вкл. **bg-dark fallback**), зеркало, VK-пилюли |
| `src/styles/card.css` | **карточка сериала** (слои, полосы, свитч) + **`badge-fade`** (анимация смены пилюль) |
| `src/styles/cards.css` | **карточки-сущности** (`.card-final`): единый стиль, палитра, пилюли, тонированные пилюли статуса, **`.card-queue`** (очереди), Нарезка (`slicing-card`) |
| `src/styles/progress.css` | **прогресс-бар** — порт Bootstrap 5.3 (.progress/.progress-bar striped/animated) |
| `src/components/StGroup/StIcon/StInput/StSelect/StBtn/StField.vue` | композиция поля = constructor-group |
| `src/components/SeriesCard.vue` | карточка сериала + `<TransitionGroup name="badge-fade">` для пилюль |
| `src/App.vue` | **парити-галерея** (разделы — см. §4); моки + симуляция анимаций |
| `src/api/schema.d.ts` | сгенерированные TS-типы из `/openapi.json` |

`node_modules`, `dist` — вне git. `schema.d.ts` — в git.

---

## 4. Ф2: статус по элементам (галерея `/v2`)

| Элемент | Статус |
|---|---|
| Поля (обычные/floating/составные) | ✅ утверждено («годно для прода») |
| Кнопки, монолитные группы | ✅ утверждено |
| Таблицы (div-table) | ✅ «безупречно» |
| Пилюли/бейджи (зеркало/очередь/VK + bg-dark) | ✅ утверждено |
| **Карточка сериала** (9 состояний) | ✅ утверждено |
| **Динамика карточки** (слои 0.8s, полосы, hover, **badge-fade** пилюль) | ✅ + **симуляция** (кнопки сценариев + авто-прогон) |
| **Карточки-сущности** (TMDB/Композиция торрент+VK/Нарезанный/Отсутствует/тест VK/Нарезка) | ✅ единый стиль, утверждено |
| **Карточки очередей Агентов** (Обработка/Загрузка/Нарезка/Мониторинг) | ✅ утверждено |
| **Прогресс-бар** (порт Bootstrap) | ✅ |
| **ToggleSwitch / SelectButton / Checkbox** | ✅ в галерее (PrimeVue) |
| **Табы** (две полосы вкладок настроек) | ⏳ **не сделано** |
| **Модалка** (PrimeVue Dialog) | ⏳ **не сделано** |

---

## 5. Дизайн-решения по карточкам (зафиксировано с пользователем)

Единый язык карточек — ядро визуальной системы. Принципы:

1. **Единая база `.card-final`** (cards.css): grid `info │ pills │ controls`,
   **радиус 9px** (токен `--border-radius`, был разнобой 8/10), padding 12 16.
   Имя класса оставлено `card-final` (не `st-card`) — чтобы реальные
   компоненты (composition/parser/chapter) при переносе не переписывать.
2. **Единая статусная палитра** (5 пастельных градиентов 135° + бордер):
   `status-success`(зелёный) / `status-pending`(жёлтый) / `card-sliced`+
   `status-sliced`(синий) / `status-excluded`(красный) / `status-archived`(серый).
3. **Раскладки пилюль — как в оригинале:** `card-torrent/compilation` →
   grid 2 колонки; `card-sliced` → 1 колонка; `card-test-result`+`card-tmdb`
   → flex column (пилюли 2-й строкой / правый столбец у края).
4. **Тонированные пилюли статуса** (`.pill.pill-primary/info/success/danger/
   secondary`): форма как `.pill`, фон слегка подкрашен в цвет — мягко, НЕ
   яркий bootstrap-бейдж. Это основной приём показа статуса в карточках.
5. **Карточки очередей Агентов** (`.card-queue`): таблицы → карточки.
   Компактно (1–2 строки, без раздувания). **Фон нейтральный серебряный**
   (как невыбранная TMDB), статус несёт тонированная пилюля (жёлтый фон
   читался как warning). `align-items: stretch` обязателен (база даёт center).
   Прогресс-бар где нужен: Загрузка/Мониторинг — полосатый + метрики
   (скорость/ETA), Нарезка — по числу глав (done/total).
6. **TMDB-результат** снят с Bootstrap `list-group`/`alert` → на `.card-final`.
7. **Анимация пилюль** (`badge-fade`, Vue TransitionGroup): появление
   scale(0.8)→1, исчезновение scale→0.6 + схлопывание ширины; соседи плавно
   сдвигаются. Раньше пилюли шли простым v-for без анимации.
8. **Нет Bootstrap reboot** — браузерные margin заголовков (h6) надо гасить
   точечно (всплыло в Нарезке: лишний отступ над «Активные главы»).

**Инвентарь карточек проекта** (где живут реальные карточки в исходнике):
Композиция (StatusTabTorrentComposition, seriesCompositionManager),
Нарезка (ChapterManager), тест VK (settingsParser), TMDB (addSeriesModal).
Пилюли-НЕ-карточки → раздел «Пилюли/бейджи»: зеркала (settingsTrackers),
бейджи очереди (settingsAgents).

---

## 6. Ф0 — что сделано (бэкенд типизирован)

- `modules/gateway/schemas.py` — модели (ApiModel extra="allow", OkResponse,
  DynamicObject, SeriesObject, …); `response_model` на **62 маршрутах**.
- **Паттерн:** nullable строго по схеме БД; объекты с null НЕ `exclude_none`;
  условные поля через extra="allow"; табличные → `DynamicObject`; success →
  `OkResponse`+exclude_none; ошибки → `responses={}`.
- Golden-харнесс `tests/api_golden.py` поймал 2 бага (500 на NULL; list-vs-
  dict). Тесты: 195 passed (красное — намеренные golden/prod-фикстуры).

---

## 7. Следующий шаг / вектор

**Закрыть Ф2** (последние два примитива):
1. **Табы** — две полосы вкладок настроек (PrimeVue Tabs под токены).
2. **Модалка** — PrimeVue Dialog (шапка/тело/футер, оверлей) под исходный вид.

**Затем развилка (согласовать):**
- **Ф3 (API-слой)** — `useApi` на openapi-fetch + `useSSE` (11 SSE-событий) +
  Pinia-сторы (§11 ТЗ). Инфраструктура данных; без неё экраны статичны.
- Дальше **Ф4** — переносить экраны на живые данные, переиспользуя готовые
  карточные раскладки из галереи (очереди, композиция, …) и под-вехи §13.

**Незакрытые риски (ТЗ §16):** SSE-инварианты (одно соединение, частичный
merge `series_updated`, `viewing`-stop при закрытии модалки); 15-мин
синхронный scan (AbortController); тесты (Vitest+Playwright); статус-модалка
и конфигуратор правил — крупные отдельные вехи Ф4.

---

## 8. Карта коммитов (ветка refactoring/bus)

```
Ф0: c31bf3c golden → c4cf52f/7b6bf5b/783c85a/2e1e4e7 fix → 82555c0 docs
Ф1: ffef404 каркас web/ под /v2
Ф2 (примитивы): 92158e8 пресет+галерея → e043a28 поля → a7afaa1 кнопки →
    3eaddc2 высота/обводка → 9a4c25c монолит → e40788c invalid → ea082ea
    таблицы → 8e84062 пилюли → c08c7a6 карточка сериала → 7422a35 docs
Ф2 (карточки): ef7eb70 высота кнопок карточки → b98da7b единый стиль
    карточек → 0190993 раскладка пилюль+hover → b2ba627 TMDB → e3047b2
    Нарезка → b6ebb6e отступы → 0d47779 badge-fade+симуляция → d654aec
    bg-dark → e4b07bc очереди→карточки → 5b420f8 тонир.пилюли+бар Нарезки
    → 379fdea нейтральный фон очередей
```
