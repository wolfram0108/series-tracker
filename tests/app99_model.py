"""A model of a video application of the App99 family (modules/sources/apps/app99.py), by its protocol: it decrypts each
request with the key of the device, checks its signature and the token of the session it opened, and answers encrypted,
some answers compressed as the application does. The cards, the players of their lines and the parsers are what a test
gives it; every request it got is in `calls`. Nothing here is a snapshot of a real answer."""
from __future__ import annotations

import json
import secrets
import zlib
from base64 import b64encode
from dataclasses import dataclass, field

import httpx

from modules.sources.apps import AppProfile
from modules.sources.apps.app99 import encrypt, signature, unseal

HOST = "https://app99.model/app/bn"
UUID = "1b4e28ba-2fa1-11d2-883f-0016d3cca427"


def profile(name: str = "banri", label: str = "半日") -> AppProfile:
    return AppProfile(name=name, label=label, protocol="app99", settings={
        "label": label, "protocol": "app99", "host": HOST, "versionName": "3.0.1", "name": "model", "package": "app.model",
        "buildNumber": "301", "buildSignature": "model-signature", "uuid": UUID})


@dataclass
class Line:
    """A line of a card: the code of its player and the items (label, token) it lists."""
    code: str
    items: list[tuple[str, str]]


@dataclass
class Card:
    id: int
    name: str
    lines: list[Line]
    sub: str = ""
    year: str = "2021"
    remarks: str = ""
    blurb: str = ""


@dataclass
class App99Model:
    # code -> the player of a line as systemInit gives it (`type` 0: the items are playlists; `parseUrl`: its parsers)
    players: dict[str, dict] = field(default_factory=dict)
    parsers: list[int] = field(default_factory=list)
    cards: dict[int, Card] = field(default_factory=dict)
    # (parser, token) -> the address it gives; a parser asked for a token it has none for gives none, as the real one
    # does (code 0, no message, no address)
    parses: dict[tuple[int, str], str] = field(default_factory=dict)
    # the cards the search gives, in its order, for any words
    found: list[dict] = field(default_factory=list)
    pages: int = 1
    calls: list[tuple[str, dict]] = field(default_factory=list)
    refuse: set[str] = field(default_factory=set)  # paths refused once each, as an ended session is
    unauthorized: set[str] = field(default_factory=set)  # paths answered once each by HTTP 401, the token being over
    said: dict[str, str] = field(default_factory=dict)  # path -> the words of its refusal, once (a quota, a VIP check)
    sessions: int = 0  # systemInit asked
    logins: int = 0  # guests opened

    def __post_init__(self) -> None:
        self._key = UUID.replace("-", "").encode()
        self._token = ""

    def line(self, code: str, name: str, *, kind: int = 1, parsers: str = "", headers: str = "{}") -> None:
        self.players[code] = {"code": code, "name": name, "type": kind, "parseUrl": parsers, "headers": headers}

    def paths(self) -> list[str]:
        return [path for path, _fields in self.calls]

    def _answer(self, data: dict, compressed: bool = False) -> httpx.Response:
        plain = json.dumps(data, ensure_ascii=False).encode()
        if compressed:
            plain = zlib.compress(plain)
        iv = secrets.token_bytes(16)
        return httpx.Response(200, text=b64encode(iv + encrypt(plain, self._key, iv)).decode())

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = "/" + str(request.url).removeprefix(HOST).lstrip("/")
        body = request.content.decode()
        fields = unseal(body, self._key)
        headers = request.headers
        token = fields.get("token", "")
        if headers.get("uuid") != UUID or headers.get("sign") != signature(headers["nonce"], headers["timestamp"], body, token):
            return httpx.Response(403, text="bad signature")
        self.calls.append((path, fields))
        if path == "/app/systemInit":
            self.sessions += 1
            return self._answer({"code": 0, "msg": "init", "player": dict(self.players),
                                 "parser_api": [{"id": each, "name": f"parser {each}"} for each in self.parsers]})
        if path == "/app/userInfo":
            self.logins += 1
            self._token = f"guest-{self.logins}"
            return self._answer({"code": 0, "msg": "ok", "userInfo": {"user_token": self._token}})
        if path in self.unauthorized:
            self.unauthorized.discard(path)
            return httpx.Response(401, text="")
        if path in self.said:
            return self._answer({"code": 1, "msg": self.said.pop(path)})
        if token != self._token or path in self.refuse:
            self.refuse.discard(path)
            return self._answer({"code": 1, "msg": "session ended"})
        if path == "/vod/search":
            return self._answer({"code": 0, "msg": "success", "data": self.found, "page_count": self.pages,
                                 "total_count": len(self.found)})
        if path == "/vod/detail":
            card = self.cards.get(int(fields["id"]))
            if card is None:
                return self._answer({"code": 0, "msg": "success", "data": None})
            return self._answer({"code": 0, "msg": "success", "data": {
                "name": card.name, "sub": card.sub, "year": card.year, "content": card.blurb,
                "play_from": "$$$".join(line.code for line in card.lines),
                "play_url": "$$$".join("#".join(f"{label}${token}" for label, token in line.items) for line in card.lines),
            }}, compressed=True)
        if path == "/app/vodParser":
            address = self.parses.get((int(fields["id"]), fields["url"]))
            if address is None:
                return self._answer({"code": 0, "msg": "", "data": ""})
            return self._answer({"code": 0, "msg": "", "data": address})
        return httpx.Response(404, text="")
