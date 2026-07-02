# CONTRACT — предпросмотр нейминга при добавлении (as-built)

> Реализованный контракт фичи: влито в `main` и задеплоено 2026-07-02, совпадает
> с реализацией. Опирается на доказанное на стенде (ROADMAP «РЕЗУЛЬТАТЫ СТЕНДА»).
> Итоговые UI-решения: вкладка открывается сразу на Шаге 3 (Тест); блок раздач —
> в рамке `modern-fieldset` (окна статуса и добавления).

## Поток целиком

```
Фронт (AddSeriesModal): вставил ссылку → POST /api/parse_url → releases[{link|magnet, torrent_id, quality}]
   │  СРАЗУ, в фоне, для всех раздач:
   ▼
POST /api/preview/naming/fetch { items:[{torrent_id, link?, magnet?}] }   → 202 { accepted:[torrent_id...] }
   │
gateway (оркестрация, без бизнес-логики):
   ├─ magnet          → command torrents.preview.fetch_magnet { torrent_id, magnet }
   └─ link (.torrent) → query sources.torrent_file.get {url} → {content_b64}
                        → command torrents.preview.parse_torrent { torrent_id, content_b64 }
   │
torrents (Р-1; владелец bencode/infohash/qBit):
   ├─ parse_torrent: локальный bencode → файлы → СОБЫТИЕ ready         (синхронно, мгновенно)
   └─ fetch_magnet:  кэш по infohash? → нет: single-flight →
                       add STARTED(category=st-naming-preview) → poll /torrents/files (timeout ~25с)
                       → files → cleanup_confirmed(infohash) → кэш → СОБЫТИЕ ready
                     (нет сидов/таймаут → СОБЫТИЕ failed)
   │  событие шины:
   ▼
torrents.preview.ready  { torrent_id, infohash, name, files:[{path,size}] }
torrents.preview.failed { torrent_id, infohash, reason }
   │  SSE (SSE_MAP)
   ▼
Фронт: собирает по torrent_id → под выбранное качество → прогон правил → рендер
```

## Топики шины (новые)

| Топик | Тип | Вход | Выход / событие | Побочки |
|---|---|---|---|---|
| `torrents.preview.parse_torrent` | command | `{torrent_id, content_b64}` | публикует `preview.ready` | нет (чистый bencode) |
| `torrents.preview.fetch_magnet` | command | `{torrent_id, magnet}` | сразу `{status: accepted\|cached}`; позже `preview.ready`/`failed` | транзиентная qBit-раздача, убирается |
| `torrents.preview.ready` | event | — | `{torrent_id, infohash, name, files:[{path,size}]}` | — |
| `torrents.preview.failed` | event | — | `{torrent_id, infohash, reason}` | — |

Оба `fetch_*` — **fire-and-forget**: хендлер спавнит задачу и возвращается; результат приходит событием. **Намеренно НЕ ресьюмабельны** (исключение из «шина-сигнал, БД-истина»): предпросмотр эфемерен, при рестарте фронт просто перезапросит; персистить нечего. В `on_start` — только BACKSTOP-очистка (ниже), не добор pending.

## Кэш и single-flight (модуль torrents)

- `_preview_cache: dict[infohash → {name, files}]` — в памяти, эфемерный (опц. TTL).
  Повторный запрос того же infohash (переоткрытие окна, другой релиз с тем же
  хешем) → мгновенный ready из кэша, qBit не трогается.
- `_preview_inflight: dict[infohash → asyncio.Task]` — single-flight: параллельные
  запросы одного infohash ждут одну задачу, а не плодят добор.

## Зомби-безопасность (доказано на стенде)

- **RUNTIME**: `cleanup_confirmed(infohash)` — `delete(deleteFiles=true)` в цикле,
  пока `torrents_info([ih])` не пуст (переживает async-add и отмену). Вызывается
  в `finally` задачи добора — независимо от исхода/отмены.
- **BACKSTOP**: preview-раздачи маркируются категорией `st-naming-preview`;
  `torrents.on_start` (reconcile) сметает всю категорию `delete(deleteFiles=true)`
  — ловит осиротевшие при краше между add и delete.
- **Идемпотентность**: qBit дедуп по infohash → быстрые переоткрытия схлопываются.

## Правки `qbt_client.py` (минимальные, аддитивные)

| Что | Зачем |
|---|---|
| `add_magnet(..., category: str\|None)` — прокинуть `category` в `/torrents/add` | маркировка preview-раздач |
| `create_category(name, save_path)` / `remove_category(name)` | завести/снять preview-категорию |
| `torrents_info(category=...)` — прокинуть `category` в params | sweep по категории |
| (опц.) `set_force_start(hashes)` | если под насыщенной очередью метаданные ждут слота (этап 2c) |

Существующие `add_magnet/torrent_files/torrents_info/delete/login` — как есть.

## Gateway

- `POST /api/preview/naming/fetch` — тело `{items:[{torrent_id, link?, magnet?}]}`,
  ответ `202 {accepted:[torrent_id...]}`. Оркеструет bus-вызовы (см. поток).
- SSE_MAP: `torrents.preview.ready` → SSE `preview_naming_ready`;
  `torrents.preview.failed` → `preview_naming_failed`.
- (Бизнес-логики нет — только резолв link→content_b64 через sources и раздача в torrents.)

## Фронт (согласовано с пользователем 2026-07-02)

**Никакого нового вида результата и нового компонента.** В окно добавления
встраивается **весь существующий раздел «Фильтры VK»** как новая вкладка,
работающий РОВНО как в Настройках.

- **Вкладка**: новая вкладка в шапке `AddSeriesModal` (паттерн `ModalShell` слот
  `header-extra`, как в `SettingsModal`), внутри — **`SettingsParser.vue`** целиком
  (3 шага: профили → правила → тест). Тест — как есть (`rules.test`, этап A, пилюли).
- **Единственная новая логика**: в поле шага 3 (`ParserTest` → `testTitles`/
  `scrapedItems`) **автоматически подставляется полный список файлов из раздачи
  выбранного качества** (basename'ы файлов), вместо ручного ввода/`ID<N>`/VK-скрейпа.
  Пользователь жмёт «тест» как обычно.
- **Префетч (чтобы без задержки)**: в `parseTorrentUrl()` после parse — фоном
  `POST /api/preview/naming/fetch` по всем качествам; списки собираются по SSE и к
  открытию вкладки готовы, падают в поле шага 3 под выбранное качество.

## РЕШЕНИЯ (сняты пользователем 2026-07-02)

1. **Что прогонять/показывать** — как в Фильтрах сейчас: тест `rules.test`
   (этап A, пилюли распознавания). Итоговые имена (`rules.format_torrent_file`,
   «этап B») — **НЕ делаем**.
2. **Объём вкладки** — **весь раздел «Фильтры VK»** (профили + правила + тест),
   не только тест. Переиспользуем `SettingsParser.vue`.
3. **Единственное дополнение к разделу** — список файлов торрента в поле шага 3.

## Детали (решено при реализации)

- **Профиль**: выбирается во вкладке (шаг 1 `SettingsParser`) и **синхронизирован**
  с `parser_profile_id` формы через `update:profileId` — что выбрал, то и сохранится.
- **basename**: в поле шага 3 кладутся имена файлов (basename), не `папка/файл.mkv`.
- **Вкладка открывается сразу на Шаге 3 (Тест)** — флаг `testFirst` (в Настройках
  без него — Шаг 2). Блок «Доступные торренты»/«Раздачи с сайта» — в `modern-fieldset`.
- Профили/правила в разделе — **глобальные** (те же объекты, что в Настройках):
  правка из окна добавления = правка везде (ожидаемо при «дублировании раздела»).
