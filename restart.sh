#!/bin/bash
# Надёжный перезапуск стенда: жёстко гасим старое (SSE-соединения браузера
# держат graceful shutdown), ждём освобождения порта, поднимаем заново.
cd "$(dirname "$0")"
pkill -9 -f "uvicorn run:app" 2>/dev/null
for i in $(seq 1 20); do
    ss -tln 2>/dev/null | grep -q ':5000 ' || break
    sleep 0.5
done
setsid nohup ./start.sh > uvicorn.log 2>&1 < /dev/null &
for i in $(seq 1 30); do
    curl -sf http://127.0.0.1:5000/api/scanner/status >/dev/null 2>&1 && { echo "поднят"; exit 0; }
    sleep 0.5
done
echo "НЕ поднялся за 15с"; tail -5 uvicorn.log; exit 1
