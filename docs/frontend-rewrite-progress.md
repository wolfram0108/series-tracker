# Прогресс переписывания фронта — точка продолжения (handoff)

> Живой снимок состояния для бесшовного продолжения после сжатия
> контекста. План/ТЗ — [docs/frontend-rewrite.md](frontend-rewrite.md)
> (решения Р-Ф1..Р-Ф12). Этот файл — что уже сделано и что дальше.
> Обновлено: 2026-06-18.

---

## 1. Где мы в плане

| Фаза | Статус |
|---|---|
| **Ф0** — типизация бэка (response_model на 62 маршрута + OpenAPI + golden) | ✅ **завершена** |
| **Ф1** — каркас `web/` (Vite+Vue3+TS+PrimeVue+Pinia) под `/v2` | ✅ **завершена** |
| **Ф2** — токен-пресет + парити-галерея (согласование визуала) | 🔄 **в работе** (см. §4) |
| Ф3 — API-слой (типы+useSSE+сторы) | ⏳ не начата |
| Ф4 — перенос экранов | ⏳ |
| Ф5 — приёмка паритета | ⏳ |
| Ф6 — cutover `/v2`→`/` | ⏳ |

Стратегия (Р-Ф7): **полная переделка целиком в `/v2`**, старый фронт
работает на `/`, переключение по полному паритету. Не инкрементально в
прод. Без срезания углов (память: `feedback-full-rewrites-no-corner-cutting`).

---

## 2. Операционка (как собирать, смотреть, проверять)

**Стенд:** Ubuntu 24.04, я работаю как **root**; приложение под systemd
от `user`. **Node 20** установлен на стенд (Ф1). Адрес снаружи:
`http://192.168.1.148:5000` (старый фронт `/`, новый `/v2`).

```bash
# Сборка нового фронта (после правок web/):
cd /home/user/series-tracker/web && npm run build      # → web/dist
chown -R user:user /home/user/series-tracker/web        # root создаёт файлы! вернуть владельца
# /v2 отдаётся сразу (gateway монтирует web/dist, рестарт НЕ нужен для статики)

# Дев-режим (если надо): cd web && npm run dev  (Vite proxy /api → :5000)
# Регенерация TS-типов из OpenAPI:
cd web && npm run gen:api                                # → src/api/schema.d.ts

# Перезапуск бэка (после правок Python):
sudo systemctl restart series-tracker

# Скриншоты для самопроверки (Playwright+firefox стоят, в т.ч. /root/.cache):
.venv/bin/python - <<'PY'
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.firefox.launch()
    pg = b.new_page(viewport={"width":1000,"height":1700})
    pg.goto("http://127.0.0.1:5000/v2/", wait_until="networkidle")  # новый
    pg.screenshot(path="/tmp/v2.png", full_page=True)
    # старый фронт: wait_until="domcontentloaded" (+wait_for_timeout) —
    #   networkidle НЕ наступает из-за вечного SSE /api/stream!
    b.close()
PY
# затем смотреть PNG инструментом Read (он показывает изображение).

# Golden-харнесс контракта (страховка additive-правок бэка, Ф0):
.venv/bin/python tests/api_golden.py capture   # эталон ДО правок
.venv/bin/python tests/api_golden.py check     # сверка ПОСЛЕ (форма не изменилась = ok)
```

**Дисциплина:** русский; без AI-атрибуции; коммит на каждый
согласованный шаг; `node` на стенде есть, но `vue-tsc` гоняется при
`npm run build` (typecheck strict — следить за неиспользуемыми импортами).

---

## 3. Что построено в `web/` (стек и файлы)

Стек (выверенные версии, НЕ bleeding-edge): Vite 6, Vue 3.5 SFC+TS 5.7,
vue-tsc 2, PrimeVue 4.2 (Aura), Pinia 2, openapi-fetch/typescript,
vuedraggable 4. (Скаффолд тянул TS6/Vite8/Pinia3 с конфликтом
openapi-typescript — запинено вручную в `web/package.json`.)

| Файл | Назначение |
|---|---|
| `web/src/main.ts` | bootstrap: Pinia + PrimeVue(STPreset, `darkModeSelector: ".st-dark"` — иначе тёмная тема браузера красила поля чёрным!) + импорт всех стилей |
| `web/src/theme/preset.ts` | `STPreset` = definePreset(Aura): primary = Bootstrap-синий #0d6efd, радиусы 9px |
| `web/src/styles/tokens.css` | порт `variables.css` (единый источник токенов) |
| `web/src/styles/overrides.css` | кнопки-градиенты (точные из `buttons.css`), поля PrimeVue: высота 46px/бордер 2px/фокус-свечение/invalid-красный, Select-высота |
| `web/src/styles/fields.css` | **порт constructor-group**: floating-label 64px, item-label-icon, item-select+options-list, item-button-group+btn-icon (градиенты) |
| `web/src/styles/tables.css` | DataTable под `div-table` (рамка, светлый жирный header, зебра) |
| `web/src/styles/pills.css` | бейдж очереди (`.badge bg-*`), зеркало (`.mirror-pill`), VK-пилюли (`.pill`/`.quality-badge`) |
| `web/src/styles/card.css` | **порт карточки**: слои layer-*, диагональные полосы+анимация, unified-text-bg, свитч, action-btn |
| `web/src/App.vue` | **парити-галерея** (временная, для согласования) |
| `web/src/components/StGroup/StIcon/StInput/StSelect/StBtn.vue` | композиция поля = constructor-group/constructor-item (монолитные группы) |
| `web/src/components/StField.vue` | удобная обёртка: одиночное floating-поле |
| `web/src/components/SeriesCard.vue` | карточка сериала (порт getLayerStyle/getAnimationClass/пилюли) |
| `web/src/api/schema.d.ts` | сгенерированные TS-типы из `/openapi.json` (gen:api) |

`web/node_modules`, `web/dist` — вне git (web/.gitignore). `schema.d.ts` —
в git.

---

## 4. Ф2: статус согласования по элементам

| Элемент | Статус | Примечание |
|---|---|---|
| **Поля ввода** (обычные + floating + составные) | ✅ **утверждено («годно для прода»)** | StField/StInput, 46px/64px, фокус-свечение, invalid-красный |
| **Кнопки** | ✅ **утверждено** | градиенты точно из buttons.css |
| **Монолитные группы** (логин+пароль, профиль+кнопки) | ✅ утверждено | StGroup + айтемы в одной рамке |
| **Таблицы** (DataTable под div-table) | ✅ **«безупречно»** | + fieldset-обёртка как у агентов |
| **Пилюли/бейджи** (3 места: очередь/зеркало/VK-предпросмотр) | ✅ утверждено | pills.css, в контексте |
| **Карточки сериала** (9 состояний) | 🔄 **на ревью у пользователя** | SeriesCard, последняя правка `c08c7a6` |
| **ToggleSwitch** (PrimeVue) | ⏳ не доделан | в проде свой вид (свитч на карточке уже портирован в card.css) |
| **SelectButton / раскрытый StSelect** | ⏳ проверить вид | |
| **Табы** (две полосы вкладок настроек) | ⏳ | PrimeVue Tabs |
| **Модалка** | ⏳ | PrimeVue Dialog |

**Подход к подгонке (важно):** PrimeVue красится производным пресетом
(токены) + точечные CSS-оверрайды под значения из старого CSS; сложные
паттерны (поле constructor-group, карточка) — **кастом-компоненты** на
тех же токенах (StField/SeriesCard). Сверка — скриншотами против старого
фронта (`/`) и его модалок.

**Эталоны-скриншоты снимались с:** главный экран (карточки), модалки
Настройки→Авторизация (поля/монолит), Трекеры (зеркала-пилюли),
Агенты (таблицы-очереди), Просмотр логов (таблица).

---

## 5. Ф0 — что сделано (бэкенд типизирован)

- `modules/gateway/schemas.py` — модели: `ApiModel`(extra="allow"),
  `OkResponse`, `ErrorResponse`, `ErrorOnly`, `DynamicObject`,
  `ScannerStatus`, `QueueTask`, `SeriesObject`, `CreatedSeries`, `TmdbInfo`.
- `response_model` навешан на **все 62 маршрута** в `api_series/api_media/
  api_system/api_settings.py`. OpenAPI включён, `/docs` доступен.
- **Паттерн (соблюдать при правках):** nullable строго по схеме БД
  (NOT NULL→required, иначе `| None`); на объектах с null-полями НЕ
  ставить `exclude_none`; условные поля не объявлять (через extra="allow");
  табличные/динамические ответы → `DynamicObject`/`list[DynamicObject]`;
  success-dict → `OkResponse`+`exclude_none`; ошибки → `responses={}`.
- Golden-харнесс `tests/api_golden.py` поймал 2 реальных бага (500 на
  NULL; list-vs-dict) — все исправлены. Тесты: 195 passed (падения —
  только намеренно-красные golden/prod-фикстуры).

---

## 6. Следующий шаг

1. **Дождаться ревью карточек** (пользователь проходит по 9 состояниям).
   Поправить по замечаниям (цвета слоёв, прозрачность подложек, иконки,
   анимация) — цикл правка → `npm run build` → chown → скриншот → показ.
2. Добить оставшиеся элементы Ф2: **ToggleSwitch, SelectButton/StSelect
   (раскрытый), табы (Tabs), модалка (Dialog)**.
3. Закрыть Ф2 → перейти к **Ф3** (API-слой: `useApi` на openapi-fetch +
   `useSSE` composable на 11 SSE-событий + сторы Pinia — см. ТЗ §11).

**Незакрытые риски/заметки (из рецензии, ТЗ §16):** SSE-инварианты
(одно соединение, частичный merge `series_updated`, `viewing`-stop при
закрытии модалки); 15-мин синхронный scan (AbortController); тесты
(Vitest+Playwright); статус-модалка и конфигуратор правил — крупные
отдельные вехи Ф4.

---

## 7. Карта коммитов Ф0–Ф2 (ветка refactoring/bus)

```
Ф0: c31bf3c golden-харнесс → c4cf52f/7b6bf5b/783c85a/(settings)/2e1e4e7 fix /82555c0 docs
Ф1: ffef404 каркас web/ под /v2
Ф2: 92158e8 пресет+галерея → e043a28 поля-фокус → a7afaa1 кнопки-градиенты
    → 3eaddc2 высота/обводка → 9a4c25c монолит-группы → e40788c invalid-fix
    → ea082ea таблицы → 8e84062 пилюли → c08c7a6 карточки
```
