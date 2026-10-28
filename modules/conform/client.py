"""HTTP/SSE client of the alignment daemon (track-muxer): reads, never writes."""
from __future__ import annotations

import json
from typing import Any, AsyncIterator

import httpx

# The daemon sends a keep-alive comment every 15 s; silence for longer means a dead connection.
STREAM_READ_TIMEOUT_S = 45.0
REQUEST_TIMEOUT_S = 10.0
# Only this registry of the daemon is mirrored; the others (downloads, mux) are skipped undecoded.
TOPIC_PREFIX = "conform."


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
