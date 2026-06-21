# legacy_frontend — АРХИВ старого фронта (НЕ редактировать как активный)

> Это **старый** фронт series-tracker (vanilla Vue 2 через CDN, без сборки):
> бывшие `static/` и `templates/` из корня. Заархивирован сюда, чтобы его
> нельзя было перепутать с боевым UI.

## Какой фронт активный

| Фронт | Где исходники | Что отдаётся | Маршрут |
|---|---|---|---|
| **Новый (боевой)** | `web/src` (Vite + Vue 3 + TS + PrimeVue) | `web/dist` (сборка) | **корень `/`** |
| Старый (архив, rollback) | `legacy_frontend/` (этот каталог) | как есть | `/legacy` + ассеты `/static` |

**Боевой UI — НОВЫЙ фронт (`web/`).** Любая правка интерфейса делается в
`web/src/...`, затем `cd web && npm run build` (пересобирает `web/dist`).
Правки в `legacy_frontend/` на боевой UI НЕ влияют — только на `/legacy`.

## Почему архив, а не удаление

`/legacy` сохранён как аварийный откат: gateway отдаёт его на корне, только
если `web/dist` отсутствует (см. `modules/gateway/module.py`, `has_dist`).
Файлы сохранены в репозитории как архив; история переносов — через `git mv`.

## Соответствие компонентов (старый → новый)

| Старый (`legacy_frontend/static/js/components`) | Новый (`web/src/components`) |
|---|---|
| `StatusTabProperties.js` | `status/StatusProperties.vue` |
| `addSeriesModal.js` | `AddSeriesModal.vue` |
| `statusModal.js` | `StatusModal.vue` |
| `settingsParser.js` | `settings/ParserTest.vue` |
