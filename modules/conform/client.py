"""HTTP/SSE client of the alignment daemon (track-muxer): the event feed, job reads and enqueue."""
from __future__ import annotations

import json
from typing import Any, AsyncIterator

import httpx

from core.reasons import reason

# The daemon sends a keep-alive comment every 15 s; silence for longer means a dead connection.
STREAM_READ_TIMEOUT_S = 45.0
REQUEST_TIMEOUT_S = 10.0
# Only this registry of the daemon is mirrored; the others (downloads, mux) are skipped undecoded.
TOPIC_PREFIX = "conform."


class DaemonRefused(RuntimeError):
    """The daemon understood the request and refused it: asking again will not help."""


class NotDelivered(RuntimeError):
    """The request certainly did not take effect at the daemon: nothing was queued, sending it again is safe."""


class Unconfirmed(RuntimeError):
    """The request may have taken effect and the daemon's answer cannot be relied on: only a look at the daemon
    tells what became of it."""


# Failures before the request left this machine, and answers that say the daemon did not take it up.
NOT_SENT = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)
TRY_AGAIN_STATUSES = (408, 425, 429)


def _json(response: httpx.Response) -> Any:
    """The body as JSON; a body that is not one is a failed answer like any other."""
    try:
        return response.json()
    except ValueError as exc:
        raise httpx.DecodingError(str(exc), request=response.request) from exc


def _problem(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__


class DaemonClient:
    def __init__(self, base_url: str, http: httpx.AsyncClient | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self._http = http or httpx.AsyncClient()

    async def info(self) -> dict[str, Any]:
        """Version and compute device of the daemon."""
        health = (await self._http.get(f"{self.base_url}/health", timeout=REQUEST_TIMEOUT_S)).json()
        device = (await self._http.get(f"{self.base_url}/conform/device",
                                       timeout=REQUEST_TIMEOUT_S)).json()
        return {"version": health.get("version"), "device": device.get("device"),
                "gpu": bool(device.get("gpu")), "gpu_name": device.get("name")}

    async def enqueue(self, body: dict[str, Any]) -> dict[str, Any]:
        """Queue one episode job; the daemon answers with the job. The outcome is told by what was proved:
        a refusal and a failure before sending are certain, and everything after the sending that is not a
        well-formed acceptance is unconfirmed."""
        try:
            response = await self._http.post(f"{self.base_url}/conform/enqueue", json=body,
                                             timeout=REQUEST_TIMEOUT_S)
        except NOT_SENT as exc:
            raise NotDelivered(_problem(exc)) from exc
        except httpx.HTTPError as exc:
            raise Unconfirmed(_problem(exc)) from exc
        if response.status_code in TRY_AGAIN_STATUSES:
            raise NotDelivered(reason("daemon_busy", status=response.status_code))
        if 400 <= response.status_code < 500:
            raise DaemonRefused(reason("daemon_refused", status=response.status_code, text=response.text[:300]))
        try:
            response.raise_for_status()
            job = response.json()
            if not isinstance(job, dict) or not job.get("id"):
                raise ValueError("the answer names no job")
        except (httpx.HTTPError, ValueError) as exc:
            raise Unconfirmed(_problem(exc)) from exc
        return job

    async def cancel(self, job_id: str) -> bool:
        """Stop a job and let the daemon drop its temporary files; False when it knows no such job."""
        response = await self._http.delete(f"{self.base_url}/conform/jobs/{job_id}", timeout=REQUEST_TIMEOUT_S)
        if response.status_code == 404:
            return False
        response.raise_for_status()
        return True

    async def jobs(self) -> list[dict[str, Any]]:
        """Every job the daemon knows."""
        response = await self._http.get(f"{self.base_url}/conform/jobs", timeout=REQUEST_TIMEOUT_S)
        response.raise_for_status()
        jobs = _json(response)
        if not isinstance(jobs, list) or not all(isinstance(each, dict) and each.get("id") for each in jobs):
            raise httpx.DecodingError("the list of jobs is not a list of jobs", request=response.request)
        return jobs

    async def job(self, job_id: str) -> dict[str, Any] | None:
        """The whole job, or None when the daemon no longer knows it."""
        response = await self._http.get(f"{self.base_url}/conform/jobs/{job_id}",
                                        timeout=REQUEST_TIMEOUT_S)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        found = _json(response)
        if not isinstance(found, dict) or str(found.get("id")) != job_id:
            raise httpx.DecodingError("the answer is not the job asked for", request=response.request)
        return found

    async def events(self) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        """(name, payload) of every conform event of the daemon, in order, until the stream ends."""
        timeout = httpx.Timeout(REQUEST_TIMEOUT_S, read=STREAM_READ_TIMEOUT_S)
        async with self._http.stream("GET", f"{self.base_url}/events", timeout=timeout) as response:
            response.raise_for_status()
            name, data = "", []
            async for line in response.aiter_lines():
                if line.startswith(":"):
                    continue
                if line:
                    field, _, value = line.partition(":")
                    value = value.removeprefix(" ")
                    if field == "event":
                        name = value
                    elif field == "data":
                        data.append(value)
                    continue
                if name.startswith(TOPIC_PREFIX) and data:
                    try:
                        yield name, json.loads("\n".join(data))
                    except json.JSONDecodeError:
                        pass
                name, data = "", []

    async def close(self) -> None:
        await self._http.aclose()
