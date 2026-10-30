"""Stand facade: production Vibix protocol plus release-page parsing."""
from __future__ import annotations

import json
import re

from modules.sources.players.vibix_protocol import (  # noqa: F401 - re-exported for the stand
    Episode, KeyState, KeyStore, Stream, VibixProtocolError,
    decode_envelope, decode_payload, decode_playlist, encode_envelope,
    envelope_bytes, parse_manifest, parse_playlist, pick_stream, recover_key,
    stream_expires)

_ALL_EPS = re.compile(r"const ALL_EPS\s*=\s*(\[.*?\]);", re.DOTALL)


def parse_all_eps(html: str) -> list[dict]:
    """Extract the release page's ALL_EPS array (episode numbers and dates)."""
    match = _ALL_EPS.search(html)
    if not match:
        raise VibixProtocolError("release page has no ALL_EPS array")
    try:
        episodes = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise VibixProtocolError("ALL_EPS is not valid JSON") from exc
    if not isinstance(episodes, list):
        raise VibixProtocolError("ALL_EPS is not a list")
    return episodes
