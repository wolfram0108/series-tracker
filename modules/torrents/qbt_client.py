"""Async qBittorrent WebAPI v2 client (decisions Р-2, Р-3, Р-5).

Policies derived from the analysis of the old system (revision.md):
- the session is ephemeral: login at start, on 403 a silent re-login and a retry
  of the failed operation; SID is never persisted (Р-3);
- 404 = resource absent: returns None, no retries;
- other network errors are retried with a pause within the `core.retry` budget;
- every call has a hard timeout (finding #7: nothing hangs forever).

Known compatibility pitfall: in qBittorrent 5.x the "add paused" parameter
was renamed from `paused` to `stopped` — we pass both.
"""
from __future__ import annotations

from typing import Any, Optional

import httpx

from core.logging import get_logger
from core.reasons import reason
from core.retry import UNTIMED, Budget, retrying


class QbtError(RuntimeError):
    """qBittorrent operation failed after all retries."""


class QbtAuthError(QbtError):
    """Login rejected — invalid credentials."""


class QbtClient:
    def __init__(self, base_url: str, username: str, password: str, *,
                 timeout: float = 20.0, retries: int = 4,
                 retry_delay: float = 2.0) -> None:
        self._username = username
        self._password = password
        self._retries = retries
        self._retry_delay = retry_delay
        self._stop_ep = "stop"      # refined in login() from the WebAPI version
        self._start_ep = "start"
        self.log = get_logger("torrents")
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            headers={"Referer": base_url.rstrip("/")},
        )

    async def close(self) -> None:
        await self._http.aclose()

    # --- session ---------------------------------------------------------------

    async def login(self) -> None:
        # API generations reply to success differently: 200 «Ok.» (4.x)
        # or 204 with no body (5.x). Reliable criterion: 2xx without «Fails.»
        # and the next authorized request passes.
        resp = await self._http.post("/api/v2/auth/login", data={
            "username": self._username, "password": self._password})
        if not resp.is_success or resp.text.strip() == "Fails.":
            raise QbtAuthError(reason("qbt_login_rejected", status=resp.status_code))
        check = await self._http.get("/api/v2/app/webapiVersion")
        if check.status_code != 200:
            raise QbtAuthError(reason("qbt_session_broken", status=check.status_code))
        # Pause endpoint names depend on the API generation:
        # WebAPI ≥ 2.11 (qBittorrent 5.x) uses stop/start, older uses pause/resume.
        api = check.text.strip()
        try:
            modern = tuple(int(x) for x in api.split(".")[:2]) >= (2, 11)
        except ValueError:
            modern = True
        self._stop_ep = "stop" if modern else "pause"
        self._start_ep = "start" if modern else "resume"
        self.log.info("сессия qBittorrent установлена (WebAPI %s)", api)

    async def _request(self, method: str, path: str, *,
                       conflict_ok: bool = False,
                       timeout: Optional[float] = None,
                       **kwargs: Any) -> Optional[httpx.Response]:
        """Request with 403/404/409/timeout policies. None — only for 404."""
        async def send() -> httpx.Response | httpx.HTTPError:
            try:
                return await self._http.request(
                    method, path,
                    timeout=timeout if timeout is not None else httpx.USE_CLIENT_DEFAULT,
                    **kwargs)
            except httpx.HTTPError as exc:
                return exc

        async def ask() -> httpx.Response | httpx.HTTPError:
            got = await send()
            if isinstance(got, httpx.Response) and got.status_code == 403:
                # Session expired — silent re-login and retry of the operation (Р-3).
                self.log.info("403 от qBittorrent — обновляю сессию")
                await self.login()
                got = await send()
            return got

        result, attempts, _exhausted = await retrying(
            # An address without a protocol is a mistake of the settings: asking again does not mend it.
            ask, final=lambda got: isinstance(got, (httpx.Response, httpx.UnsupportedProtocol)),
            budget=Budget(attempts=self._retries, pauses=(self._retry_delay,), limit_s=UNTIMED),
            on_retry=lambda got, attempt, _pause: self.log.warning(
                "ошибка %s %s (попытка %d/%d): %s", method, path, attempt, self._retries, got))
        if not isinstance(result, httpx.Response):
            raise QbtError(reason("qbt_retries_exhausted", method=method, path=path,
                                  attempts=attempts, detail=str(result)))
        if result.status_code == 404:
            return None
        if result.status_code == 409 and conflict_ok:
            return result  # meaningful "already exists" reply (5.x)
        result.raise_for_status()
        return result

    # --- operations ---------------------------------------------------------------

    async def version(self) -> str:
        resp = await self._request("GET", "/api/v2/app/version")
        return resp.text

    @staticmethod
    def _add_verdict(resp: httpx.Response) -> str:
        # Duplicate: 4.x replies 200 «Fails.», 5.x replies 409 Conflict.
        if resp.status_code == 409 or "Fails." in resp.text:
            return "fails"
        return "ok"

    async def add_torrent_file(self, content: bytes, save_path: str, *,
                               paused: bool = True,
                               category: str | None = None) -> str:
        """Adds a .torrent. Returns 'ok' | 'fails' (= already exists?)."""
        flag = "true" if paused else "false"
        data = {"savepath": save_path, "paused": flag, "stopped": flag}
        if category:
            data["category"] = category
        resp = await self._request(
            "POST", "/api/v2/torrents/add", conflict_ok=True,
            data=data,
            files={"torrents": ("file.torrent", content,
                                "application/x-bittorrent")})
        return self._add_verdict(resp)

    async def add_magnet(self, magnet: str, save_path: str, *,
                         paused: bool = True,
                         category: str | None = None) -> str:
        flag = "true" if paused else "false"
        data = {"urls": magnet, "savepath": save_path,
                "paused": flag, "stopped": flag}
        if category:
            data["category"] = category
        resp = await self._request(
            "POST", "/api/v2/torrents/add", conflict_ok=True, data=data)
        return self._add_verdict(resp)

    async def torrents_info(self, hashes: list[str] | None = None, *,
                            category: str | None = None) -> list[dict]:
        params: dict[str, str] = {}
        if hashes:
            params["hashes"] = "|".join(hashes)
        if category is not None:
            params["category"] = category
        resp = await self._request("GET", "/api/v2/torrents/info", params=params)
        return resp.json() if resp else []

    async def create_category(self, name: str, save_path: str = "") -> None:
        """Creates a qBit category (idempotent: already exists → 409, swallowed)."""
        await self._request("POST", "/api/v2/torrents/createCategory",
                            conflict_ok=True,
                            data={"category": name, "savePath": save_path})

    async def default_save_path(self) -> str:
        """Default save path — for transient preview torrents."""
        resp = await self._request("GET", "/api/v2/app/preferences")
        return (resp.json() or {}).get("save_path", "") if resp else ""

    async def find_hash(self, candidates: list[str], *,
                        attempts: int = 5, delay: float = 0.5) -> Optional[str]:
        """Which of the candidates qBittorrent keys the torrent by (Р-2).

        Several short attempts: the torrent shows up in the list almost
        instantly, but "almost" is not "strictly before the API replies".
        """
        async def look() -> Optional[str]:
            known = {t["hash"] for t in await self.torrents_info(candidates)}
            return next((candidate for candidate in candidates if candidate in known), None)

        found, _attempts, _exhausted = await retrying(
            look, final=lambda hit: hit is not None,
            budget=Budget(attempts=attempts, pauses=(delay,), limit_s=UNTIMED))
        return found

    async def torrent_files(self, torrent_hash: str) -> Optional[list[dict]]:
        resp = await self._request("GET", "/api/v2/torrents/files",
                                   params={"hash": torrent_hash})
        return resp.json() if resp else None

    async def set_file_priority(self, torrent_hash: str, indices: list[int],
                                priority: int) -> None:
        """File priority of the torrent (0 — do not download)."""
        if not indices:
            return
        await self._request("POST", "/api/v2/torrents/filePrio",
                            data={"hash": torrent_hash,
                                  "id": "|".join(str(i) for i in indices),
                                  "priority": priority})

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
