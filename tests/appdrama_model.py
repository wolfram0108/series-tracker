"""A model of an application of the AppDrama family by its protocol (modules/sources/apps/appdrama.py): the navigator
names the host; the key of the zone is given to a request signed by the public key of the profile (the model holds the
private one and checks the sign); a request of the API is read the way the server reads it — the body out of its protobuf,
the random prefix taken away, AES-ECB under the data key, the query parsed — and answered by a protobuf."""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
from urllib.parse import parse_qsl

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from modules.sources.apps import AppProfile
from modules.sources.apps.appdrama import field as pb, fields

NAVIGATOR = "https://navigator.model/config.json"
HOST = "https://drama.model"
DATA_KEY, DATA_IV, PARAMS_KEY = "0123456789abcdef", "fedcba9876543210", "abcdefghijklmnop"


def _key_pair():
    private = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    public = private.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return private, base64.b64encode(public).decode()


@dataclass
class Video:
    line: str
    line_name: str
    title: str
    path: str
    vip: bool = False


@dataclass
class DramaModel:
    name: str = "huayi"
    label: str = "华谊"
    cards: dict[int, tuple[str, int, list[Video]]] = field(default_factory=dict)
    found: list[tuple[int, str, int, str]] = field(default_factory=list)  # the search: (id, name, year, remark)
    plays: dict[tuple[str, str], tuple[str, dict]] = field(default_factory=dict)  # (line, token) -> (address, headers)
    # (line, token) the application answers with no address and no words (as it does under load).
    silent: set[tuple[str, str]] = field(default_factory=set)
    navigator_down: bool = False
    calls: list[tuple[str, dict]] = field(default_factory=list)
    zones: int = 0

    def __post_init__(self) -> None:
        self.private, self.public = _key_pair()
        self.zone_private, self.zone_public = _key_pair()

    def profile(self) -> AppProfile:
        return AppProfile(name=self.name, label=self.label, protocol="appdrama", settings={
            "host": NAVIGATOR, "fallback": HOST, "publicKey": self.public, "dataKey": DATA_KEY, "dataIv": DATA_IV,
            "paramsKey": PARAMS_KEY, "pkg": "com.model.drama", "appName": "Drama", "version": "3.0.19"})

    def _signed(self, sign: str, private) -> str:
        return private.decrypt(base64.b64decode(sign), padding.PKCS1v15()).decode()

    def _query(self, body: bytes) -> dict:
        found = dict(fields(body))
        combined = found[1].decode() + found[2].decode()
        decryptor = Cipher(algorithms.AES(DATA_KEY.encode()), modes.ECB()).decryptor()
        plain = decryptor.update(base64.b64decode(combined[8:])) + decryptor.finalize()
        text = plain[:-plain[-1]].decode()
        stamp = str(found[4])
        assert text.endswith(stamp), "the query ends with the time of the request"
        return dict(parse_qsl(text[: -len(stamp)], keep_blank_values=True))

    def _answer(self, data: bytes, code: int = 200, message: str = "success") -> httpx.Response:
        return httpx.Response(200, content=pb(1, code) + pb(2, message) + pb(3, data))

    def handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == NAVIGATOR:
            return httpx.Response(503) if self.navigator_down else httpx.Response(200, json={"domain": HOST})
        path = request.url.path
        assert "paramsData" in request.headers.get("publicParams", ""), "every request describes the device"
        if path == "/api/v5/find/app/zone":
            found = dict(fields(request.content))
            assert self._signed(found[2].decode(), self.private) == f"{found[1]}{found[4].decode()}"
            self.zones += 1
            key = self.zone_public
            quarter = len(key) // 4
            parts = [key[:quarter], key[quarter:2 * quarter], key[2 * quarter:3 * quarter], key[3 * quarter:]]
            return self._answer(pb(1, "s1") + pb(2, parts[0]) + pb(3, parts[1]) + pb(4, parts[2]) + pb(5, parts[3]))
        query = self._query(request.content)
        self.calls.append((path, query))
        if path == "/api/proto/v5/drama/getDetail":
            card = self.cards.get(int(query["id"]))
            if card is None:
                return self._answer(b"", 404, "not found")
            name, year, videos = card
            data = pb(4, int(query["id"])) + pb(9, name) + pb(18, year)
            for number, video in enumerate(videos, start=1):
                data += pb(29, pb(1, number) + pb(2, video.title) + pb(4, video.path) + pb(9, video.line)
                           + pb(10, video.line_name) + pb(14, int(video.vip)))
            return self._answer(data)
        if path == "/api/proto/v5/videoUsableUrl":
            address, headers = self.plays.get((query["vodPlayFrom"], query["playUrl"]), ("", {}))
            data = pb(1, address) + b"".join(pb(6, pb(1, key) + pb(2, value)) for key, value in headers.items())
            key = (query["vodPlayFrom"], query["playUrl"])
            return self._answer(data + (b"" if address or key in self.silent else pb(9, "解析失败")))
        if path == "/api/proto/v5/drama/search":
            data = b"".join(pb(1, pb(3, number) + pb(5, name) + pb(13, remark) + pb(14, year))
                            for number, name, year, remark in self.found)
            return self._answer(data)
        return httpx.Response(404)

    def paths(self) -> list[str]:
        return [path for path, _query in self.calls]
