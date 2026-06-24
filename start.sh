#!/bin/bash
# Запуск новой системы (этап 6). Локальная конфигурация и секреты — в
# .env рядом со скриптом (вне git; шаблон — .env.example). ST_DB_PATH по
# умолчанию app.db в каталоге проекта, поэтому в .env не обязателен.
cd "$(dirname "$0")"
[ -f .env ] && { set -a; . ./.env; set +a; }
# SSE-соединения бесконечны: без лимита graceful shutdown рестарт
# зависает, пока открыт браузер.
# --proxy-headers: доверять X-Forwarded-* ТОЛЬКО от reverse-proxy
# (ST_FORWARDED_IPS — адрес nginx; Этап 2). Без env — только loopback.
exec .venv/bin/python -m uvicorn run:app --host 0.0.0.0 --port 5000 \
    --timeout-graceful-shutdown 5 \
    --proxy-headers --forwarded-allow-ips "${ST_FORWARDED_IPS:-127.0.0.1}"
