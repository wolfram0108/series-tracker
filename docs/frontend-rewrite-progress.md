# Прогресс переписывания фронта — точка продолжения (handoff)

> Живой снимок для бесшовного продолжения после сжатия контекста.
> План/ТЗ — [docs/frontend-rewrite.md](frontend-rewrite.md) (Р-Ф1..Р-Ф12,
> фазы, §13 под-вехи Ф4). Обновлено: 2026-06-18 (Ф3 закрыта, Ф4 в работе).

---

## 1. Где мы в плане

| Фаза | Статус |
|---|---|
| **Ф0** — типизация бэка (response_model 62 + OpenAPI + golden) | ✅ |
| **Ф1** — каркас `web/` (Vite+Vue3+TS+PrimeVue+Pinia) под `/v2` | ✅ |
| **Ф2** — токен-пресет + парити-галерея + окно настроек | ✅ |
| **Ф3** — API-слой (client+useSSE+сторы+диспетчер) | ✅ |
| **Ф4** — перенос экранов на живые данные | 🔄 **в работе** (см. §4) |
| Ф5 — приёмка паритета | ⏳ |
| Ф6 — cutover `/v2`→`/` | ⏳ |

**`/v2` теперь — реальное приложение** (не галерея): `App.vue` = главный
экран на живых данных стенда. Галерея сохранена в `Gallery.vue` (полигон,
не монтируется).

**РЕЖИМ РАБОТЫ (важно):** Ф4 ведётся **автономно по ТЗ** — пользователя
звать **только на визуальную приёмку** готовых экранов; техническое/
архитектурное/порядок под-вех решать самому (память
`feedback-autonomous-frontend-rewrite`). На приёмку зову скриншотом.

---

## 2. Операционка

**Стенд:** root-сессия; приложение под systemd от `user`. Node 20. Адрес:
`http://192.168.1.148:5000` (старый фронт `/`, новый `/v2`).

```bash
cd /home/user/series-tracker/web && npm run build   # → dist (vue-tsc strict!)
chown -R user:user /home/user/series-tracker/web     # root создаёт файлы — вернуть владельца
npm run test                                         # Vitest (юнит сторов)
# /v2 отдаётся сразу (gateway монтирует dist; рестарт не нужен для статики)
# Скриншот: Playwright firefox, /v2 — wait_until="domcontentloaded" (+timeout),
#   т.к. SSE /api/stream вечный → networkidle НЕ наступает (это норма, SSE жив).
sudo systemctl restart series-tracker                # после правок Python
```

**Дисциплина:** русский; без AI-атрибуции; **не вписывать реальные данные
стенда (логины/пароли) в коммиты** (классификатор блокирует); коммит на шаг.

---

## 3. Архитектура `web/src` (Ф3 + Ф4)

```
api/client.ts ─ openapi-fetch на schema.d.ts (62 маршрута)
composables/
  useApi.ts ─ request(): loading + ошибки→Toast (для компонентов)
  useSSE.ts ─ singleton, 1 EventSource /api/stream, 11 событий, on()/connect()
  useRealtime.ts ─ диспетчер SSE→сторы (вызывается в main.ts)
  useConfirm.ts ─ Promise-подтверждение (singleton state)
stores/ (Pinia, setup-стиль)
  series.ts ─ список + merge series_updated + savingIds; series.test.ts (6 Vitest)
  queues.ts ─ 4 очереди (agent/torrents/downloads/slicing)
  scanner.ts ─ ScannerStatus + load
  indicators.ts ─ computed (monitoring/downloader/slicing) — см. находку §6
  ui.ts ─ activeSeriesId + viewing-цикл (open→['viewing'], close→[])
components/
  AppHeader.vue ─ заголовок + индикаторы + кнопки
  SeriesCard.vue ─ карточка на реальной Series (emit scan/delete/toggle/open-status)
  ModalShell.vue ─ оболочка модалок (оверлей, header/body/footer слоты, Esc/клик-вне)
  ConfirmDialog.vue ─ окно подтверждения (useConfirm)
  SettingsModal.vue ─ окно настроек (xl, фикс-высота 86vh) + вкладки-сегмент
  settings/SettingsAuth|Trackers|Agents|Debug.vue ─ вкладки
  LogsModal.vue ─ просмотр логов (фильтры + таблица)
  AddSeriesModal.vue ─ КАРКАС (этап 1) добавления
  St*.vue ─ поля constructor-group (из Ф2)
main.ts ─ bootstrap + setupRealtime() + load series/scanner
Gallery.vue ─ сохранённая парити-галерея Ф2 (полигон, не монтируется)
styles/ ─ tokens/overrides/fields/tables/pills/card/cards/progress/modal/layout.css
```

---

## 4. Ф4: статус под-вех

| Под-веха (§13) | Статус | Эндпоинты |
|---|---|---|
| **Главный экран** (список+карточки, live) | ✅ принят | GET /api/series, scan/toggle/delete |
| **Подтверждение** + ModalShell | ✅ | DELETE /api/series/{id}?delete_from_qb |
| **Настройки: Авторизация** | ✅ принят | GET/POST /api/auth |
| **Настройки: Трекеры** | ✅ принят | GET /api/trackers, PUT /api/trackers/{id} |
| **Настройки: Агенты** (очереди-карточки) | ✅ принят | queuesStore (SSE) |
| **Настройки: Отладка** (сканер+saved_paths) | ✅ принят | /api/scanner/settings, scan_all, /api/settings/saved_paths |
| **Просмотр логов** | ✅ | GET /api/logs?group&level&limit |
| **Add-модалка** (полный порт) | ✅ на приёмке | parse_url, tmdb/search+details, parser-profiles, saved_paths, POST /api/series |
| ↳ SavedPathDropdown + качество (anilibria/astar) + VK + TMDB | ✅ | catalogName, sortEpisodeKeys, vk_search_mode |
| **Статус-модалка** (Props/Composition/Slicing/History) | ⏳ крупная | |
| **Конфигуратор Фильтров VK** (DnD) | ⏳ крупная | см. §5 |
| Отладка-доп (БД-просмотр/очистка/флаги) | ⏳ | /api/settings/{force_replace,less_strict_scan,...} |

Окно настроек: вкладки-сегмент (остров облегает, адаптивное схлопывание
в иконки @media, стабильная ширина призраком жирного текста, высота 39px),
фикс-высота окна 86vh (легаси-паттерн — не прыгает при переключении вкладок).

---

## 5. Конфигуратор правил VK (разобран, веха ⏳)

«Фильтры VK» = Парсер (`settingsParser.js`, 763 стр). Три шага: Профили
(✅ StGroup), Тестирование (✅ card-test-result), **Редактор правил
(Шаг 2) — НЕ сделан**. Модель:
- правило = `conditions[]` (ЕСЛИ: `_blocks[]` + AND/OR) + `actions[]`
  (ТО: `action_type` + `_action_blocks[]`).
- блок = `{id, type, value?}`; 8 типов: text(ред.)/number/whitespace/
  any_text/start_of_line/end_of_line + add/subtract(ред., только ТО).
- DnD: **vuedraggable@4** (уже в deps) + палитра clone; **contenteditable**
  для значений; CSS — порт `rule-editor.css`. API: GET /api/parser-profiles,
  POST /api/parser-profiles/test. Логика блоков (перенести 1:1): cloneBlock,
  isBlockEditable, getBlockDisplayText (number в ТО → «Число #N»),
  updateBlockValue, getBlockClasses. Риски §6.2 (contenteditable, drag).

---

## 6. Находки и Ф0

- **НАХОДКА (Ф3):** `_updateIndicatorState` (hold-таймер 1000ms, на него
  ссылался §11 ТЗ) в исходном app.js **мёртвый** (не вызывается). Индикаторы
  мгновенные → `indicatorsStore` = computed-производные от очередей/сканера.
- **Ф0:** `schemas.py` (62 response_model, extra="allow"), golden-харнесс
  `tests/api_golden.py`. Тесты: 195 passed + 6 Vitest (seriesStore merge).

---

## 7. Следующий шаг

1. ~~Доделать Add-модалку~~ ✅ (порт целиком: TMDB, SavedPathDropdown,
   VK search/get_all, качество anilibria/astar). Попутно исправлен баг
   saved_paths в Отладке (обёртка {paths:[]}). Ждёт визуальной приёмки.
2. **Статус-модалка** — крупная веха (Properties/Composition/Slicing/History
   + ChapterManager). uiStore.openStatus уже ставит viewing.
3. **Конфигуратор Фильтров VK** — крупная веха DnD (§5).
4. **Отладка-доп** + модалка DatabaseViewer.
5. Затем Ф5 (приёмка паритета + e2e Playwright) → Ф6 (cutover).

**Незакрытые инварианты для проверки в Ф5:** частичный merge series_updated
(есть), viewing-stop при закрытии статус-модалки (uiStore.closeStatus —
подключить в статус-модалке), одно SSE-соединение (есть), 15-мин scan
(AbortController — добавить при необходимости).

---

## 8. Карта коммитов (ветка refactoring/bus)

```
Ф3: 254b43e client+useApi → 1176f1f useSSE → d015812 seriesStore+Vitest
    → ea811d6 сторы+диспетчер
Ф4: 6ec2e47 главный экран → 3d96aa2 ModalShell+подтверждение → d3b64a7
    окно настроек+Агенты → 694ec77 фикс-высота → 305c794 Авторизация →
    804cd7a Трекеры → 4fb844c Отладка → 8ee89c5 Логи → 8f93836 Add-каркас
(ранее: Ф0 c31bf3c.., Ф1 ffef404, Ф2 92158e8..9043c45 + окно настроек
 2f9fa90..9185a3d)
```
