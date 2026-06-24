# Безопасность series-tracker — план и чек-лист

> Статус: в работе. Документ — план приведения приложения к состоянию,
> когда аудит безопасности даёт «зелёный свет». Ведётся по этапам; каждый
> этап — отдельный согласованный коммит с проверкой.
> Связано: [STATUS.md](STATUS.md), [deployment.md](deployment.md),
> находка про утечку секрета — [../contracts/findings.md](../contracts/findings.md).

---

## 1. Контекст и модель угроз

- Приложение **однопользовательское**, но **сетевое** (FastAPI/uvicorn).
- Планируется **доступ из интернета** (порт наружу) → планка строгая.
- **Привязка действий к пользователям не нужна**; нужен один аккаунт
  **администратора** (вход — да; роли/журнал по людям — нет).
- Транспортная модель — **как у Nextcloud / qBittorrent**: TLS терминирует
  **внешний reverse-proxy (nginx)**; приложение слушает обычный `http` на
  loopback за ним. nginx сам по себе дверь не запирает — вход, защита
  куков, защита от перебора и заголовки делает **приложение**.

```
Интернет ──https──> nginx (TLS, :443) ──http──> uvicorn (127.0.0.1:5000) ──> gateway
```

### Кого защищаем от чего
| Угроза | Пример | Закрывается этапом |
|---|---|---|
| Чужой заходит и всё делает | удалил серии, прочитал настройки | 1 (вход) |
| Подслушивание трафика | перехват пароля трекера в сети | 2 (TLS+куки) |
| Утечка секретов | пароли в БД/в git/на фронте | 0, 3 |
| Перебор пароля | бот молотит /login из интернета | 1 (rate-limit+бан) |
| Подмена адреса/хоста | обход бана, host-injection | 2 (trusted proxy/hosts) |
| Опасные ручки | очистка таблиц, SSRF, /docs | 4 |

---

## 2. Текущее состояние (находки аудита)

| # | Находка | Severity | Где |
|---|---|---|---|
| S1 | Бэкап БД с реальными секретами в публичном git (`app.db.rules-bak`) | **critical** | git history (`a5851ae`) |
| S2 | Нет аутентификации ни на одном эндпоинте | **high → critical** (из интернета) | gateway, все роуты |
| S3 | `GET /api/auth` отдаёт все учётки на фронт в открытом виде | **high → critical** | `api_settings.py` |
| S4 | Секреты в БД хранятся в открытом виде | high | `auth`, `settings`, `tracker_sessions` |
| S5 | `POST /api/database/clear_table` (DELETE) без защиты | medium → high | `api_settings.py` |
| S6 | SSRF: `/api/parse_url` ходит по любому URL клиента | medium | `api_settings.py` → `sources` |
| S7 | Публичные `/docs` и `/openapi.json` | low | `gateway/module.py` |
| S8 | Нет TLS (трафик по http) | high (из интернета) | транспорт |
| S9 | Нет защитных заголовков (HSTS/CSP/…) | low/medium | gateway |

Что **уже хорошо** (не трогаем): SQL параметризован, subprocess без shell,
имена файлов санитизируются, `verify=False` нет, eval/pickle нет, CORS не
открыт, текущие `.env`/`app.db` вне git.

---

## 3. Целевая модель

- **Вход:** один админ. Логин + пароль; пароль — хэш **argon2**. После
  входа — серверная сессия в **защищённой куке** (Secure, HttpOnly,
  SameSite=Strict). Единый «замок» (middleware) перед всеми запросами,
  кроме страницы входа, логина и статики.
- **Перебор:** лимит неудачных попыток + временный бан по IP (реальный IP
  берём из заголовка от **доверенного** nginx).
- **Транспорт:** TLS на nginx; приложение — `--proxy-headers`,
  `forwarded-allow-ips` = адрес nginx; проверка хоста (TrustedHost); HSTS.
- **Секреты:** на фронт не уходят никогда (флаг «задано/нет» + `●●●●`); в
  БД — шифрование (Fernet), мастер-ключ `ST_SECRET_KEY` в `.env` (вне git).
- **Подчистка:** `/docs` выкл (или под вход), опасные ручки за замком +
  allowlist доменов для parse_url, защитные заголовки, без стек-трейсов.

Новые зависимости (бэкенд): `argon2-cffi`, `cryptography`, `itsdangerous`
(или штатный Starlette session). Фронт: экран входа + обработка 401.

---

## 4. Этапы

### Этап 0 — утечка секрета (СРОЧНО, независимо от остального)
1. `.gitignore`: добавить `app.db*` и `*.bak` (текущий `*.db` не ловит `.rules-bak`).
2. Убрать файл из git (`git rm --cached app.db.rules-bak`) + коммит.
3. **Сменить все засветившиеся секреты** (см. §6) — обязательно: файл уже
   был в публичной истории, секреты считаем скомпрометированными.
4. Переписать историю git (удалить файл из всех коммитов) + `force-push`.
   Необратимо, затрагивает публичный origin → выполняется по отдельному
   подтверждению.

### Этап 1 — вход администратора
- Хранение: логин + argon2-хэш пароля (в `settings` или отдельной таблице
  владельца). Первичная установка пароля — через `.env`
  (`ST_ADMIN_USER`/`ST_ADMIN_PASSWORD`) при первом старте.
- Эндпоинты: `POST /api/login`, `POST /api/logout`, `GET /api/me`.
- Middleware-замок в gateway: всё, кроме `/login`, статики и health —
  требует валидную сессию, иначе `401`.
- Защита от перебора: счётчик неудач по IP + экспоненциальная задержка/бан.
- Фронт: экран входа; на `401` — переход на него.
- ⏳ TODO (визуал): оформление модалки входа ЧЕРНОВОЕ (нейтральное
  затемнение + лёгкое размытие). Финальный вид — эффект «матового стекла»
  (frosted glass) — вынесен в отдельный ресёрч; функционально вход готов.

### Этап 2 — готовность к reverse-proxy и транспорт
- uvicorn: `--proxy-headers --forwarded-allow-ips <nginx_ip>`.
- Куки: Secure + HttpOnly + SameSite=Strict.
- `TrustedHostMiddleware` (список разрешённых доменов).
- Заголовок HSTS (или на nginx).
- Документация: готовый пример конфига nginx (§7).

### Этап 3 — секреты
- `GET /api/auth` и аналоги: НЕ возвращать значения секретов; только
  `{"configured": true/false}`. Фронт показывает `●●●●`, меняет вводом нового.
- Шифрование в БД: Fernet, ключ `ST_SECRET_KEY` из `.env`. Прозрачный
  слой шифровать/расшифровать в репозиториях-владельцах (`trackerauth`,
  `settings`). Миграция существующих значений в шифртекст.

### Этап 4 — подчистка
- `/docs`, `/openapi.json`: выключить (или под вход).
- `clear_table`, `parse_url`: под замок (уже будут за gate) + parse_url —
  allowlist доменов трекеров (анти-SSRF).
- Защитные заголовки: CSP, X-Content-Type-Options, Referrer-Policy,
  frame-ancestors.
- Ошибки наружу — без стек-трейсов/деталей стека.

### Этап 5 — проверка
- Статический скан кода (`bandit`), чек-лист §5, ручная проверка входа и
  ограничения доступа. Зафиксировать «было красное → стало зелёное».

---

## 5. Чек-лист соответствия (что должно стать зелёным)

- [ ] Все эндпоинты, кроме входа/статики, требуют аутентификацию.
- [ ] Пароль админа хранится хэшем (argon2), не в открытом виде.
- [ ] Защита от перебора пароля (лимит + бан по IP).
- [ ] TLS включён (через nginx); HTTP редиректит на HTTPS.
- [ ] Куки: Secure, HttpOnly, SameSite.
- [ ] Проверка хоста (TrustedHost), доверие только своему proxy.
- [ ] Защитные заголовки: HSTS, CSP, X-Content-Type-Options, Referrer-Policy.
- [ ] Секреты не уходят на фронт; в БД зашифрованы; ключ вне git.
- [ ] Нет секретов в git и его истории.
- [ ] `/docs` закрыт; опасные ручки под замком; parse_url с allowlist.
- [ ] Наружу не уходят стек-трейсы и версии.

---

## 6. Секреты к ротации (Этап 0, шаг 3)

Сменить/перевыпустить (значения не приводятся намеренно):
- **qBittorrent** — пароль WebUI (+ обновить `.env` `ST_QBIT_*`).
- **Трекеры** — пароли kinozal, rutracker (учётки в БД, вводятся через UI).
- **VK** — пароль/токен учётки.
- **TMDB** — перевыпустить API-токен (`settings.tmdb_token`).

---

## 7. Пример конфигурации nginx (для Этапа 2)

```nginx
server {
    listen 443 ssl http2;
    server_name series.example.com;

    ssl_certificate     /etc/letsencrypt/live/series.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/series.example.com/privkey.pem;

    # HSTS — браузер ходит только по https
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_http_version 1.1;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        # SSE: не буферизовать поток событий
        proxy_buffering off;
        proxy_read_timeout 3600s;
    }
}
server {                      # http -> https
    listen 80;
    server_name series.example.com;
    return 301 https://$host$request_uri;
}
```

uvicorn за этим nginx запускать с:
`uvicorn run:app --host 127.0.0.1 --port 5000 --proxy-headers --forwarded-allow-ips 127.0.0.1`
