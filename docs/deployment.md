# Развёртывание (production runbook)

Пошаговое развёртывание и обновление Series Tracker v2. Прод запускается под
systemd (uvicorn :5000), фронт собирается на месте, БД — SQLite + Alembic.
Развёртывание — **только из git** (ветка `main`), без копирования отдельных
файлов.

---

## 1. Требования к машине

| Компонент | Версия / примечание |
|---|---|
| ОС | Linux (systemd) |
| Python | 3.12 |
| Node.js | 20 (для сборки фронта) |
| ffmpeg, yt-dlp | в `PATH` (нарезка и VK-загрузка) |
| Firefox | для Playwright — браузерная доставка astar/anilibria_tv |
| qBittorrent | WebUI API доступен (на стенде — Docker-контейнер, `:8080`) |
| NAS / хранилище | смонтировано (пути сохранения сериалов) |

Системные пакеты ставит root; приложение работает от непривилегированного
пользователя (на стенде — `user`).

---

## 2. Первичное развёртывание

```bash
# 1. Код (ветка main)
git clone -b main https://github.com/wolfram0108/series-tracker.git
cd series-tracker

# 2. Бэкенд: окружение и зависимости
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/playwright install firefox        # для astar/anilibria_tv

# 3. БД: миграции
ST_DB_PATH=app.db .venv/bin/alembic upgrade head

# 4. Секреты запуска
cp .env.example .env
#   заполнить ST_QBIT_URL / ST_QBIT_USER / ST_QBIT_PASS
#   (без ST_QBIT_URL модуль torrents не стартует)

# 5. Фронт: сборка (отдаётся бэкендом с корня /)
cd web && npm ci && npm run build && cd ..
```

Учётки трекеров, VK, TMDB-токен вносятся **через UI** (Настройки → авторизация)
после первого запуска и хранятся в БД — в `.env` их нет.

### systemd-юнит

`/etc/systemd/system/series-tracker.service`:

```ini
[Unit]
Description=Series Tracker — uvicorn :5000
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=exec
User=user
Group=user
WorkingDirectory=/home/user/series-tracker
ExecStart=/home/user/series-tracker/start.sh
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

`start.sh` (в репо) подхватывает `.env` и запускает
`uvicorn run:app --host 0.0.0.0 --port 5000 --timeout-graceful-shutdown 5`
(лимит нужен: SSE-соединения бесконечны, иначе рестарт виснет, пока открыт
браузер).

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now series-tracker
```

### Проверка

```bash
systemctl is-active series-tracker                 # active
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:5000/   # 200
grep '"запущено модулей: 13"' logs/info.log | tail -1             # все модули
```

UI — `http://<host>:5000/`. Старый фронт (аварийный откат) — `/legacy`.

---

## 3. Обновление (redeploy)

```bash
cd /home/user/series-tracker
git pull origin main
.venv/bin/pip install -r requirements.txt          # если менялись зависимости
.venv/bin/alembic upgrade head                     # если есть новые миграции
cd web && npm ci && npm run build && cd ..          # пересобрать фронт
sudo systemctl restart series-tracker
```

Рабочие данные (`app.db`, `.venv`, `logs/`, `web/dist`) — вне git
(см. `.gitignore`), при `git pull` сохраняются. `web/dist` собирается на месте
и в репозиторий не коммитится — **не забыть `npm run build`** после правок
фронта, иначе UI останется старым.

---

## 4. Резервная копия и откат

**Перед миграцией/обновлением** — бэкап БД:

```bash
sudo systemctl stop series-tracker
cp app.db "app.db.bak.$(date +%Y%m%d-%H%M%S)"      # дату подставит shell
sudo systemctl start series-tracker
```

**Откат кода** на предыдущий коммит:

```bash
git checkout <предыдущий-коммит-или-тег>
.venv/bin/alembic downgrade <ревизия>              # если откатывали миграцию
cd web && npm ci && npm run build && cd ..
sudo systemctl restart series-tracker
```

**Откат на старую систему (v1):** код заархивирован в теге `legacy/v1-final`
(`git checkout legacy/v1-final`). Это другая архитектура и другой формат
запуска — применять только как крайнюю меру, с отдельной БД/конфигом.

---

## 5. Эксплуатация

- **Логи:** JSON в `logs/*.log` (error/warning/info/debug, ротация
  RotatingFileHandler). В UI — модалка «Логи» (фильтр групп динамический).
  Каталог логов переопределяется `ST_LOG_DIR`.
- **Перезапуск:** `sudo systemctl restart series-tracker` или `./restart.sh`.
- **Переживание перезагрузки:** юнит `enabled` + `Restart=always`; Docker →
  qBit (`restart=unless-stopped`) → приложение поднимаются сами.
- **Crash-tolerance:** при старте модули добирают незавершённые задачи из БД;
  `torrents` сверяется с qBit (осиротевшие раздачи сбрасываются).
- **Переменные окружения** (`.env`): `ST_QBIT_URL/USER/PASS` (обязательно для
  торрентов), опц. `ST_DB_PATH` (по умолчанию `app.db`), `ST_LOG_DIR`
  (по умолчанию `logs`).

---

## 6. Чек-лист релиза

- [ ] Полный набор тестов зелёный (с golden/прод-фикстурами для
      `*_match_production`).
- [ ] Миграции применены на **копии** прод-БД без ошибок.
- [ ] Фронт пересобран (`npm run build`), корень отдаёт свежий бандл.
- [ ] `.env` заполнен, qBittorrent доступен.
- [ ] Бэкап `app.db` снят.
- [ ] Сервис `active`, `/` → 200, 13 модулей в логах старта.
- [ ] Откат подготовлен (тег/коммит + бэкап БД).
