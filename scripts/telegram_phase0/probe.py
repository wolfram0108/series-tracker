#!/usr/bin/env python3
"""
Фаза 0: проверка Telegram до интеграции в Series Tracker.

Что делает:
  1) Авторизация (сессия Telethon в файле рядом со скриптом).
  2) Разрешение чата/канала/группы и выборка сообщений с видео-документами.
  3) Опционально — тестовая загрузка одного файла на диск.

Подготовка:
  - Зарегистрируйте приложение на https://my.telegram.org → api_id, api_hash.
  - Зависимости — в venv приложения (из корня репозитория). Папка может называться
    venv или .venv — смотрите, что есть у вас:
      venv/bin/pip install -r requirements.txt
      # или: .venv/bin/pip install -r requirements.txt
  - Запуск тем же Python:
      venv/bin/python scripts/telegram_phase0/probe.py list --chat ...

Переменные окружения:
  TELEGRAM_API_ID     — число (обязательно)
  TELEGRAM_API_HASH   — строка (обязательно)
  TELEGRAM_PHONE      — +7999... (подставляется при первом входе, если не передать --phone)

Примеры:
  export TELEGRAM_API_ID=12345 TELEGRAM_API_HASH=abcdef...
  python probe.py list --chat @mychannel --limit 15
  python probe.py download-first --chat @mychannel --dir /tmp/tg_test --progress
  python probe.py search --chat t.me/ShowPVA -q 'Почтенный мастер' --csv-out /tmp/pva.csv
  # то же, явно: --scan server (по умолчанию). Старый перебор ленты: --scan local --max-messages 20000
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import sys
from pathlib import Path

from telethon import TelegramClient
from telethon.errors import RPCError
from telethon.tl.types import DocumentAttributeFilename, DocumentAttributeVideo, MessageMediaDocument

# Сессия хранится рядом со скриптом (не коммитить — в .gitignore).
SESSION_PATH = Path(__file__).resolve().parent / "telegram_phase0.session"


def _env_api() -> tuple[int, str]:
    raw_id = os.environ.get("TELEGRAM_API_ID", "").strip()
    api_hash = os.environ.get("TELEGRAM_API_HASH", "").strip()
    if not raw_id or not api_hash:
        print(
            "Задайте TELEGRAM_API_ID и TELEGRAM_API_HASH (см. https://my.telegram.org).",
            file=sys.stderr,
        )
        sys.exit(1)
    try:
        return int(raw_id), api_hash
    except ValueError:
        print("TELEGRAM_API_ID должен быть целым числом.", file=sys.stderr)
        sys.exit(1)


def _caption_text(message) -> str:
    """Текст поста (подпись к видео) — то, что пишут люди; без подписи пустая строка."""
    if message.message and message.message.strip():
        return message.message.strip()
    return ""


def _document_filename_only(message) -> str:
    """Имя файла внутри Telegram (часто «кривое»), только из атрибутов документа."""
    media = message.media
    if not isinstance(media, MessageMediaDocument) or not media.document:
        return ""
    for attr in media.document.attributes or []:
        if isinstance(attr, DocumentAttributeFilename):
            return (attr.file_name or "").strip()
    return ""


def _video_title(message) -> str:
    """Как раньше: для списка — сначала подпись, иначе имя файла."""
    cap = _caption_text(message)
    if cap:
        return cap[:200]
    fn = _document_filename_only(message)
    return fn if fn else "(no title)"


def _document_id(message) -> int | None:
    media = message.media
    if isinstance(media, MessageMediaDocument) and media.document:
        return media.document.id
    return None


def _text_for_search(message, match_on: str) -> tuple[str, str]:
    """
    Возвращает (строка для поиска, метка откуда брали: caption | filename | caption+filename).
    match_on: caption_only | filename_only | caption_or_filename
    """
    cap = _caption_text(message)
    fn = _document_filename_only(message)
    if match_on == "caption_only":
        return cap, "caption"
    if match_on == "filename_only":
        return fn, "filename"
    # caption_or_filename
    parts = [p for p in (cap, fn) if p]
    joined = "\n".join(parts)
    return joined, "caption+filename" if cap and fn else ("caption" if cap else "filename")


def _queries_match(haystack: str, queries: list[str], case_insensitive: bool) -> bool:
    if not haystack or not queries:
        return False
    if case_insensitive:
        h = haystack.lower()
        return any(q.lower() in h for q in queries)
    return any(q in haystack for q in queries)


def _is_video_document(message) -> bool:
    media = message.media
    if not isinstance(media, MessageMediaDocument) or not media.document:
        return False
    for attr in media.document.attributes or []:
        if isinstance(attr, DocumentAttributeVideo):
            return True
    # Иногда приходит как документ без Video-атрибута — оставляем строгий фильтр для фазы 0.
    return False


async def _client(phone: str | None) -> TelegramClient:
    api_id, api_hash = _env_api()
    client = TelegramClient(str(SESSION_PATH), api_id, api_hash)
    await client.start(phone=phone or os.environ.get("TELEGRAM_PHONE"))
    return client


async def cmd_list(chat: str, limit: int, phone: str | None) -> None:
    client = await _client(phone)
    try:
        entity = await client.get_entity(chat)
        print(f"Чат: {getattr(entity, 'title', None) or getattr(entity, 'username', chat)} (id={entity.id})")
        n = 0
        async for message in client.iter_messages(entity, limit=500):
            if not _is_video_document(message):
                continue
            n += 1
            title = _video_title(message)
            date = message.date.isoformat() if message.date else ""
            print(f"  [{n}] msg_id={message.id} date={date}")
            print(f"      title: {title!r}")
            if n >= limit:
                break
        if n == 0:
            print("Видео-документов в последних сообщениях не найдено (лимит обхода 500).")
    finally:
        await client.disconnect()


def _row_from_message(message, chat_id: int, matched_field: str, search_engine: str) -> dict:
    cap = _caption_text(message)
    doc_fn = _document_filename_only(message)
    return {
        "chat_id": chat_id,
        "message_id": message.id,
        "date": message.date.isoformat() if message.date else "",
        "caption": cap,
        "document_filename": doc_fn,
        "document_id": _document_id(message),
        "search_matched_on": matched_field,
        "search_engine": search_engine,
    }


async def cmd_search(
    chat: str,
    queries: list[str],
    phone: str | None,
    scan: str,
    max_messages: int,
    max_results_per_query: int,
    match_on: str,
    case_insensitive: bool,
    json_out: Path | None,
    csv_out: Path | None,
) -> None:
    """
    Ищет видео-посты. Режим server — поиск на стороне Telegram (как в приложении),
    без перебора «последних N сообщений». Режим local — перебор и фильтр у клиента.
    """
    client = await _client(phone)
    rows_by_id: dict[int, dict] = {}
    try:
        entity = await client.get_entity(chat)
        chat_id = entity.id
        title = getattr(entity, "title", None) or getattr(entity, "username", chat)
        print(f"Чат: {title} (id={chat_id})", file=sys.stderr)

        if scan == "server":
            if match_on == "filename_only":
                print(
                    "Предупреждение: серверный поиск Telegram обычно не ищет по имени файла. "
                    "Используйте --scan local --match-on filename_only.",
                    file=sys.stderr,
                )
            print(
                f"Серверный поиск (Telegram API), до {max_results_per_query} результатов на каждый --query; "
                f"затем отбор только постов с видео-файлом.",
                file=sys.stderr,
            )
            print(f"Запросы: {queries!r}, уточнение поля: {match_on}", file=sys.stderr)

            for q in queries:
                got = 0
                async for message in client.iter_messages(
                    entity, search=q, limit=max_results_per_query
                ):
                    got += 1
                    if not _is_video_document(message):
                        continue
                    haystack, matched_field = _text_for_search(message, match_on)
                    if not _queries_match(haystack, [q], case_insensitive):
                        continue
                    rows_by_id[message.id] = _row_from_message(
                        message, chat_id, matched_field, "telegram_server"
                    )
                print(f"  запрос {q!r}: просмотрено ответов поиска: {got}", file=sys.stderr)

        else:
            print(
                f"Локальный скан: до {max_messages} сообщений подряд, фильтр по подстроке.",
                file=sys.stderr,
            )
            print(f"Подстроки: {queries!r}, режим поля: {match_on}", file=sys.stderr)
            scanned = 0
            async for message in client.iter_messages(entity, limit=max_messages):
                scanned += 1
                if not _is_video_document(message):
                    continue
                haystack, matched_field = _text_for_search(message, match_on)
                if not _queries_match(haystack, queries, case_insensitive):
                    continue
                rows_by_id[message.id] = _row_from_message(
                    message, chat_id, matched_field, "local_scan"
                )
            print(f"Просмотрено сообщений: {scanned}", file=sys.stderr)

        rows = sorted(rows_by_id.values(), key=lambda r: r["message_id"], reverse=True)
        print(f"Итого постов с видео после фильтра: {len(rows)}", file=sys.stderr)

        if json_out:
            json_out.parent.mkdir(parents=True, exist_ok=True)
            with open(json_out, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "chat_id": chat_id,
                        "scan": scan,
                        "query": queries,
                        "match_on": match_on,
                        "results": rows,
                    },
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
            print(f"JSON: {json_out}", file=sys.stderr)

        if csv_out:
            csv_out.parent.mkdir(parents=True, exist_ok=True)
            fieldnames = [
                "chat_id",
                "message_id",
                "date",
                "caption",
                "document_filename",
                "document_id",
                "search_matched_on",
                "search_engine",
            ]
            with open(csv_out, "w", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
                w.writeheader()
                w.writerows(rows)
            print(f"CSV: {csv_out}", file=sys.stderr)

        # Человекочитаемая таблица в stdout (краткие подписи)
        for r in rows:
            cap_preview = (r["caption"] or "").replace("\n", " ")[:120]
            if len(r["caption"] or "") > 120:
                cap_preview += "…"
            print(
                f"msg_id={r['message_id']}\tdate={r['date']}\n"
                f"  caption:   {cap_preview!r}\n"
                f"  file:      {r['document_filename']!r}\n"
                f"  matched:   {r['search_matched_on']}\n"
            )
    finally:
        await client.disconnect()


def _make_download_progress_callback():
    """Колбэк Telethon: (получено байт, всего байт). total может быть 0/None до известности размера."""
    last_pct = [-1]

    def progress_callback(received: int, total: int) -> None:
        if total and total > 0:
            pct = min(100, int(100 * received / total))
            if pct != last_pct[0]:
                last_pct[0] = pct
                mb_r = received / (1024 * 1024)
                mb_t = total / (1024 * 1024)
                print(
                    f"\r  прогресс: {pct}% ({mb_r:.1f} / {mb_t:.1f} MiB)",
                    end="",
                    file=sys.stderr,
                    flush=True,
                )
        else:
            mb = received / (1024 * 1024)
            print(f"\r  скачано: {mb:.1f} MiB (размер ещё неизвестен)", end="", file=sys.stderr, flush=True)

    return progress_callback


async def cmd_download_first(
    chat: str, out_dir: Path, phone: str | None, show_progress: bool
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    client = await _client(phone)
    try:
        entity = await client.get_entity(chat)
        target = None
        async for message in client.iter_messages(entity, limit=500):
            if _is_video_document(message):
                target = message
                break
        if not target:
            print("Нет подходящего видео-сообщения для загрузки.", file=sys.stderr)
            sys.exit(2)
        print(f"Загрузка msg_id={target.id} → {out_dir} ...", file=sys.stderr if show_progress else sys.stdout)
        cb = _make_download_progress_callback() if show_progress else None
        path = await client.download_media(target, file=str(out_dir), progress_callback=cb)
        if show_progress:
            print(file=sys.stderr)
        print(f"Готово: {path}")
    except RPCError as e:
        print(f"Ошибка Telegram API: {e}", file=sys.stderr)
        sys.exit(3)
    finally:
        await client.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser(description="Telegram phase-0 probe (Telethon)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="Показать первые N видео из чата")
    p_list.add_argument("--chat", required=True, help="@username, ссылка t.me/..., или числовой id")
    p_list.add_argument("--limit", type=int, default=10)
    p_list.add_argument("--phone", help="Телефон для первого входа (+7999...), иначе TELEGRAM_PHONE")

    p_dl = sub.add_parser("download-first", help="Скачать первое найденное видео из чата")
    p_dl.add_argument("--chat", required=True)
    p_dl.add_argument("--dir", type=Path, required=True, help="Каталог для файла")
    p_dl.add_argument(
        "--progress",
        action="store_true",
        help="Показывать прогресс в stderr (колбэк Telethon download_media)",
    )
    p_dl.add_argument("--phone")

    p_search = sub.add_parser(
        "search",
        help="Найти видео-посты по подстроке в тексте поста (caption); таблица msg_id ↔ подпись ↔ файл",
    )
    p_search.add_argument("--chat", required=True)
    p_search.add_argument(
        "--query",
        "-q",
        action="append",
        dest="queries",
        required=True,
        help="Подстрока поиска (можно несколько раз: совпадение по ЛЮБОЙ из них)",
    )
    p_search.add_argument(
        "--scan",
        choices=("server", "local"),
        default="server",
        help="server — поиск на стороне Telegram (как в приложении), без перебора ленты. "
        "local — перебор последних сообщений и фильтр по подстроке.",
    )
    p_search.add_argument(
        "--max-results",
        type=int,
        default=2000,
        dest="max_results_per_query",
        help="Только для --scan server: максимум сообщений на один --query (пагинация ответа поиска).",
    )
    p_search.add_argument(
        "--max-messages",
        type=int,
        default=5000,
        help="Только для --scan local: сколько последних сообщений просмотреть.",
    )
    p_search.add_argument(
        "--match-on",
        choices=("caption_only", "caption_or_filename", "filename_only"),
        default="caption_only",
        help="Где искать: только подпись (рекомендуется), подпись ИЛИ имя файла, только имя файла",
    )
    p_search.add_argument(
        "--case-sensitive",
        action="store_true",
        help="Учитывать регистр (по умолчанию регистр игнорируется)",
    )
    p_search.add_argument("--json-out", type=Path, help="Сохранить полный результат в JSON")
    p_search.add_argument("--csv-out", type=Path, help="Сохранить таблицу в CSV")
    p_search.add_argument("--phone")

    args = parser.parse_args()
    phone = getattr(args, "phone", None)

    if args.cmd == "list":
        asyncio.run(cmd_list(args.chat, args.limit, phone))
    elif args.cmd == "download-first":
        asyncio.run(
            cmd_download_first(args.chat, args.dir, phone, show_progress=args.progress)
        )
    elif args.cmd == "search":
        asyncio.run(
            cmd_search(
                args.chat,
                args.queries,
                phone,
                scan=args.scan,
                max_messages=args.max_messages,
                max_results_per_query=args.max_results_per_query,
                match_on=args.match_on,
                case_insensitive=not args.case_sensitive,
                json_out=args.json_out,
                csv_out=args.csv_out,
            )
        )
    else:
        parser.error("Неизвестная команда")


if __name__ == "__main__":
    main()
