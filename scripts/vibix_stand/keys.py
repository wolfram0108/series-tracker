"""Global Vibix decoder-key store: last key, 12-hour TTL, manifest recovery."""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

from .protocol import VibixProtocolError, decode_payload, recover_key

logger = logging.getLogger("vibix.keys")

KEY_TTL_S = 12 * 3600
DEFAULT_PATH = Path(os.environ.get(
    "VIBIX_KEY_STORE", "~/.cache/series-tracker/vibix-key.json")).expanduser()


@dataclass(frozen=True)
class KeyState:
    key: bytes
    obtained_at: int
    source: str = "recovered"


class KeyStore:
    """One shared key file per machine; readers never block on the network."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else DEFAULT_PATH

    def load(self) -> KeyState | None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return KeyState(key=bytes.fromhex(str(data["key_hex"])),
                            obtained_at=int(data["obtained_at"]),
                            source=str(data.get("source", "unknown")))
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def save(self, key: bytes, now: int, source: str = "recovered") -> KeyState:
        state = KeyState(key=key, obtained_at=now, source=source)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_name(self.path.name + ".tmp")
        temp.write_text(json.dumps({"key_hex": key.hex(), "obtained_at": now,
                                    "source": source}), encoding="utf-8")
        os.chmod(temp, 0o600)
        temp.replace(self.path)
        return state

    def resolve(self, *, api_body: bytes, manifest_body: bytes | None,
                now: int | None = None) -> tuple[bytes, bool]:
        """Return (key, refreshed): reuse last key, refresh on TTL or failure."""
        moment = int(time.time()) if now is None else now
        state = self.load()
        if state is not None and moment - state.obtained_at < KEY_TTL_S:
            if _decodes(api_body, state.key):
                return state.key, False
            logger.warning("cached Vibix key stopped decoding; recovering")

        if manifest_body is not None:
            try:
                key = recover_key(manifest_body, api_body)
            except VibixProtocolError as exc:
                logger.warning("Vibix key recovery failed: %s", exc)
            else:
                self.save(key, moment)
                return key, True

        if state is not None and _decodes(api_body, state.key):
            logger.warning("using stale Vibix key obtained at %s", state.obtained_at)
            return state.key, False
        raise VibixProtocolError(
            "cannot obtain Vibix decoder key: no encrypted manifest in the capture")


def _decodes(body: bytes, key: bytes) -> bool:
    try:
        decode_payload(body, key)
    except VibixProtocolError:
        return False
    return True
