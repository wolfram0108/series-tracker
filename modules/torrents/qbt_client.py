"""Асинхронный клиент qBittorrent WebAPI v2 (решения Р-2, Р-3, Р-5).

Политики, выведенные разбором старой системы (revision.md):
- сессия эфемерна: логин при старте, на 403 — тихий релогин и повтор
  сорванной операции; SID нигде не персистится (Р-3);
- 404 = ресурса нет: возвращается None, без ретраев;
- таймаут long-poll `sync/maindata` — норма, не ретраить;
- прочие сетевые ошибки — ретраи с паузой;
- у каждого вызова жёсткий таймаут (находка №7: ничто не виснет вечно).

Известная грабля совместимости: в qBittorrent 5.x параметр добавления
«на паузе» переименован из `paused` в `stopped` — передаём оба.
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

import httpx

from core.logging import get_logger


class QbtError(RuntimeError):
    """Операция с qBittorrent не удалась после всех повторов."""


class QbtAuthError(QbtError):
    """Логин отвергнут — неверные учётные данные."""


class QbtClient:
    def __init__(self, base_url: str, username: str, password: str, *,
                 timeout: float = 20.0, retries: int = 4,
                 retry_delay: float = 2.0) -> None:
        self._username = username
        self._password = password
        self._retries = retries
        self._retry_delay = retry_delay
        self._stop_ep = "stop"      # уточняется в login() по версии WebAPI
        self._start_ep = "start"
        self.log = get_logger("torrents")
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            headers={"Referer": base_url.rstrip("/")},
        )

    async def close(self) -> None:
        await self._http.aclose()

    # --- сессия ---------------------------------------------------------------

    async def login(self) -> None:
        # Поколения API отвечают на успех по-разному: 200 «Ok.» (4.x)
        # или 204 без тела (5.x). Надёжный критерий: 2xx без «Fails.»
        # и последующий авторизованный запрос проходит.
        resp = await self._http.post("/api/v2/auth/login", data={
            "username": self._username, "password": self._password})
        if not resp.is_success or resp.text.strip() == "Fails.":
            raise QbtAuthError(
                f"qBittorrent отверг логин (HTTP {resp.status_code})")
        check = await self._http.get("/api/v2/app/webapiVersion")
        if check.status_code != 200:
            raise QbtAuthError(
                f"логин принят, но сессия не работает (HTTP {check.status_code})")
        # Имена эндпоинтов паузы зависят от поколения API:
        # WebAPI ≥ 2.11 (qBittorrent 5.x) — stop/start, старше — pause/resume.
        api = check.text.strip()
        try:
            modern = tuple(int(x) for x in api.split(".")[:2]) >= (2, 11)
        except ValueError:
            modern = True
        self._stop_ep = "stop" if modern else "pause"
        self._start_ep = "start" if modern else "resume"
        self.log.info("сессия qBittorrent установлена (WebAPI %s)", api)

    async def _request(self, method: str, path: str, *,
                       no_retry_on_timeout: bool = False,
                       conflict_ok: bool = False,
                       timeout: Optional[float] = None,
                       **kwargs: Any) -> Optional[httpx.Response]:
        """Запрос с политиками 403/404/409/таймаут. None — только для 404."""
        last_error: Exception | None = None
        for attempt in range(1, self._retries + 1):
            try:
                resp = await self._http.request(
                    method, path,
                    timeout=timeout if timeout is not None else httpx.USE_CLIENT_DEFAULT,
                    **kwargs)
            except httpx.TimeoutException as exc:
                if no_retry_on_timeout:
                    return None  # long-poll: тишина — это норма
                last_error = exc
                self.log.warning("таймаут %s %s (попытка %d/%d)",
                                 method, path, attempt, self._retries)
            except httpx.HTTPError as exc:
                last_error = exc
                self.log.warning("ошибка %s %s (попытка %d/%d): %s",
                                 method, path, attempt, self._retries, exc)
            else:
                if resp.status_code == 403:
                    # Сессия протухла — тихий релогин и повтор операции (Р-3).
                    self.log.info("403 от qBittorrent — обновляю сессию")
                    await self.login()
                    continue
                if resp.status_code == 404:
                    return None
                if resp.status_code == 409 and conflict_ok:
                    return resp  # осмысленный ответ «уже существует» (5.x)
                resp.raise_for_status()
                return resp
            if attempt < self._retries:
                await asyncio.sleep(self._retry_delay)
        raise QbtError(f"{method} {path}: не удалось за {self._retries} "
                       f"попыток ({last_error})")

    # --- операции ---------------------------------------------------------------

    async def version(self) -> str:
        resp = await self._request("GET", "/api/v2/app/version")
        return resp.text

    @staticmethod
    def _add_verdict(resp: httpx.Response) -> str:
        # Дубликат: 4.x отвечает 200 «Fails.», 5.x — 409 Conflict.
        if resp.status_code == 409 or "Fails." in resp.text:
            return "fails"
        return "ok"

    async def add_torrent_file(self, content: bytes, save_path: str, *,
                               paused: bool = True) -> str:
        """Добавляет .torrent. Возвращает 'ok' | 'fails' (= уже существует?)."""
        flag = "true" if paused else "false"
        resp = await self._request(
            "POST", "/api/v2/torrents/add", conflict_ok=True,
            data={"savepath": save_path, "paused": flag, "stopped": flag},
            files={"torrents": ("file.torrent", content,
                                "application/x-bittorrent")})
        return self._add_verdict(resp)

    async def add_magnet(self, magnet: str, save_path: str, *,
                         paused: bool = True) -> str:
        flag = "true" if paused else "false"
        resp = await self._request(
            "POST", "/api/v2/torrents/add", conflict_ok=True,
            data={"urls": magnet, "savepath": save_path,
                  "paused": flag, "stopped": flag})
        return self._add_verdict(resp)

    async def torrents_info(self, hashes: list[str] | None = None) -> list[dict]:
        params = {"hashes": "|".join(hashes)} if hashes else {}
        resp = await self._request("GET", "/api/v2/torrents/info", params=params)
        return resp.json() if resp else []

    async def find_hash(self, candidates: list[str], *,
                        attempts: int = 5, delay: float = 0.5) -> Optional[str]:
        """Каким из кандидатов infohash qBittorrent ключует торрент (Р-2).

        Несколько коротких попыток: торрент появляется в списке почти
        мгновенно, но «почти» — не «строго до ответа API».
        """
        for _ in range(attempts):
            known = {t["hash"] for t in await self.torrents_info(candidates)}
            for candidate in candidates:
                if candidate in known:
                    return candidate
            await asyncio.sleep(delay)
        return None

    async def torrent_files(self, torrent_hash: str) -> Optional[list[dict]]:
        resp = await self._request("GET", "/api/v2/torrents/files",
                                   params={"hash": torrent_hash})
        return resp.json() if resp else None

    async def pause(self, hashes: list[str]) -> None:
        await self._request("POST", f"/api/v2/torrents/{self._stop_ep}",
                            data={"hashes": "|".join(hashes)})

    async def resume(self, hashes: list[str]) -> None:
        await self._request("POST", f"/api/v2/torrents/{self._start_ep}",
                            data={"hashes": "|".join(hashes)})

    async def recheck(self, hashes: list[str]) -> None:
        await self._request("POST", "/api/v2/torrents/recheck",
                            data={"hashes": "|".join(hashes)})

    async def delete(self, hashes: list[str], *, delete_files: bool) -> None:
        await self._request("POST", "/api/v2/torrents/delete",
                            data={"hashes": "|".join(hashes),
                                  "deleteFiles": str(delete_files).lower()})

    async def rename_file(self, torrent_hash: str, old_path: str,
                          new_path: str) -> None:
        await self._request("POST", "/api/v2/torrents/renameFile",
                            data={"hash": torrent_hash, "oldPath": old_path,
                                  "newPath": new_path})

    async def set_location(self, torrent_hash: str, location: str) -> None:
        await self._request("POST", "/api/v2/torrents/setLocation",
                            data={"hashes": torrent_hash, "location": location})

    async def sync_maindata(self, rid: int) -> Optional[dict]:
        resp = await self._request("GET", "/api/v2/sync/maindata",
                                   params={"rid": rid}, timeout=35.0,
                                   no_retry_on_timeout=True)
        return resp.json() if resp else None
