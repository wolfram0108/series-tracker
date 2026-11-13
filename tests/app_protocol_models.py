"""Models of the video applications of every protocol the service reads besides App99 and AppDrama, for the stand of the
whole application (tests/app_stand.py): AppGet (茉莉), Gulu (咕噜), YunDuo (布布), AppMN (米诺), AppV7 (小猪) and the public shares of 139 Cloud. Each answers by its
protocol as its client asks it (modules/sources/apps/<protocol>.py) — the keys, the sealing, the session, the ticket —
with the cards a scenario gives it: a card is a name and one line of episodes, each episode a token whose stream is an
address on the host of the stand. Nothing here is a snapshot of a real answer."""
from __future__ import annotations

import base64
import json
import zlib
from dataclasses import dataclass, field as dataclass_field
from urllib.parse import parse_qsl

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from modules.sources.apps import AppProfile
from modules.sources.apps.appdrama import field, fields
from modules.sources.apps.appget import encrypt
from modules.sources.apps.appv7 import api_key
from modules.sources.apps.gulu import cbc, first, message, opened, sealed, session_key


@dataclass
class Card:
    number: int
    name: str
    line: str
    items: list[tuple[str, str]]  # (label, token)


@dataclass
class _Model:
    """What every model holds: its cards, the stream of each token, and the requests it got."""
    cards: dict[int, Card] = dataclass_field(default_factory=dict)
    streams: dict[str, str] = dataclass_field(default_factory=dict)  # token -> the address of its stream
    asked: list[str] = dataclass_field(default_factory=list)

    def card(self, number: int, name: str, line: str, episodes: list[tuple[str, str, str]]) -> None:
        """A card with one line: each episode (label, token, the address of its stream)."""
        self.cards[number] = Card(number, name, line, [(label, token) for label, token, _ in episodes])
        self.streams.update({token: address for _label, token, address in episodes})


# --- AppGet (茉莉): the list of hosts, JSON sealed by AES-CBC under the keys of the profile ---------------------------

class AppGetModel(_Model):
    KEY, IV = "0123456789abcdef", "fedcba9876543210"
    SITE, HOST, PARSER = "https://moli-site.model/hosts.txt", "https://moli-api.model", "https://moli-parser.model/p?url="

    def profile(self) -> AppProfile:
        return AppProfile("moli", "茉莉", "appget", {"label": "茉莉", "protocol": "appget", "dataKey": self.KEY,
                                                    "dataIv": self.IV, "site": self.SITE})

    def owns(self, url: str) -> bool:
        return url.startswith((self.SITE, self.HOST, self.PARSER))

    def _sealed(self, payload: dict) -> httpx.Response:
        return httpx.Response(200, json={"code": 1, "msg": "ok", "data": encrypt(json.dumps(payload, ensure_ascii=False),
                                                                             self.KEY, self.IV)})

    def handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.asked.append(url)
        if url == self.SITE:
            return httpx.Response(200, text=f"{self.HOST}\n")
        if url.startswith(self.PARSER):
            # The parser of the line names the stream of the episode in JSON.
            return httpx.Response(200, json={"code": 200, "url": self.streams[request.url.params["url"]]})
        method = request.url.path.rsplit("/", 1)[-1]
        if method == "vodDetail":
            card = self.cards.get(int(json.loads(request.content)["vod_id"]))
            if card is None:
                return self._sealed({})
            return self._sealed({"vod": {"vod_name": card.name, "vod_year": "2021"}, "vod_play_list": [{
                "player_info": {"show": card.line, "parse": self.PARSER},
                "urls": [{"name": label, "url": token, "from": card.line, "nid": place, "token": "",
                          "parse_api_url": self.PARSER + token} for place, (label, token) in enumerate(card.items, 1)]}]})
        if method == "searchList":
            return self._sealed({"search_list": [{"vod_id": card.number, "vod_name": card.name, "vod_year": "2021"}
                                                 for card in self.cards.values()]})
        return httpx.Response(404)


# --- Gulu (咕噜): a handshake by ECDH, then protobuf sealed under the key of the session -------------------------------

class GuluModel(_Model):
    ENDPOINT, PARSER = "https://gulu.model/app/v2", "https://gulu-parser.model/api?url="
    PARSER_ID = 38

    def __init__(self) -> None:
        super().__init__()
        self.keys: dict[str, bytes] = {}

    def profile(self) -> AppProfile:
        return AppProfile("gulu", "咕噜", "gulu", {"label": "咕噜", "protocol": "gulu", "endpoint": self.ENDPOINT})

    def owns(self, url: str) -> bool:
        return url.startswith((self.ENDPOINT, self.PARSER))

    def _boot(self) -> bytes:
        lines = {card.line for card in self.cards.values()}
        players = b"".join(field(4, message((1, number), (2, code), (3, code), (4, code), (8, bytes([self.PARSER_ID]))))
                           for number, code in enumerate(sorted(lines), start=100))
        return players + field(7, message((1, self.PARSER_ID), (2, "parser"), (3, self.PARSER), (4, "json"), (10, "url")))

    def handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.asked.append(url)
        if url.startswith(self.PARSER):
            return httpx.Response(200, json={"code": 200, "url": self.streams[request.url.params["url"]]})
        if "X-Handshake-Key" in request.headers:
            wrap = request.headers["X-Handshake-Key"].encode()
            raw = base64.b64decode(request.content)
            hello = zlib.decompress(cbc(raw[16:], wrap, raw[:16], decrypt=True))
            private = ec.generate_private_key(ec.SECP256R1())
            shared = private.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), first(hello, 1)))
            session = f"s{len(self.keys) + 1}"
            self.keys[session] = session_key(shared, session)
            public = private.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
            iv = bytes(16)
            return httpx.Response(200, content=base64.b64encode(iv + cbc(zlib.compress(message((1, session), (2, public))), wrap, iv)))
        key = self.keys.get(request.headers.get("X-Session-Id", ""))
        if key is None:
            return httpx.Response(200, content=message((1, 0), (3, "session expired")))
        plain = opened(key, first(request.content, 2))
        method = (first(plain, 2), first(plain, 3))
        payload = first(plain, 6) or b""  # the request is (stamp, major, minor, device, -, payload, stamp)
        if method == (1, 0):
            body = self._boot()
        elif method == (3, 62):
            card = self.cards.get(first(payload, 1))
            body = (message((1, card.number), (5, card.name))
                    + field(75, message((1, card.line), *((2, message((3, token), (4, label))) for label, token in card.items)))
                    if card is not None else b"")
        elif method == (3, 61):
            body = b"".join(field(1, field(3, message((1, card.number), (2, 2021), (3, card.name)))) for card in self.cards.values())
        else:
            return httpx.Response(200, content=message((1, 0), (2, sealed(key, field(3, "no such request"), bytes(range(12))))))
        return httpx.Response(200, content=message((1, 0), (2, sealed(key, field(4, body), bytes(range(12))))))


# --- YunDuo (布布): the catalogue in JSON, the stream by one signed protobuf ----------------------------------------------

class YunDuoModel(_Model):
    HOST = "https://yunduo.model"

    def profile(self) -> AppProfile:
        return AppProfile("yunduo", "布布", "yunduo", {"label": "布布", "protocol": "yunduo", "host": self.HOST, "id": "f",
                                                     "dev": "d", "sk": "s", "web_sign": "w", "x_client": "x"})

    def owns(self, url: str) -> bool:
        return url.startswith(self.HOST)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.asked.append(str(request.url))
        path = request.url.path.removeprefix("/api.php/web")
        if path == "/vod/get_detail":
            card = self.cards.get(int(request.url.params["vod_id"]))
            if card is None:
                return httpx.Response(200, json={"code": 200, "data": []})
            return httpx.Response(200, json={"code": 200, "data": [{
                "vod_id": card.number, "vod_name": card.name, "vod_year": "2021", "vod_play_from": card.line,
                "vod_play_url": "#".join(f"{label}${token}" for label, token in card.items)}],
                "vodplayer": [{"from": card.line, "show": card.line}]})
        if path == "/search/index":
            return httpx.Response(200, json={"code": 200, "pageCount": 1, "total": len(self.cards), "data": [
                {"vod_id": card.number, "vod_name": card.name, "vod_year": "2021"} for card in self.cards.values()]})
        if path == "/decode/url":
            token = dict(fields(request.content))[1].decode()
            return httpx.Response(200, content=field(1, 1) + field(2, "ok") + field(3, self.streams[token]))
        return httpx.Response(404)


# --- AppMN (米诺): a guest session, the ticket of an episode, its parser ----------------------------------------------------

class AppMnModel(_Model):
    HOST = "https://appmn.model"

    def profile(self) -> AppProfile:
        return AppProfile("appmn", "米诺", "appmn", {"label": "米诺", "protocol": "appmn", "host": self.HOST,
                                                   "client_pub": "p", "device_id": "0123456789abcdef", "so_version": "13"})

    def owns(self, url: str) -> bool:
        return url.startswith(self.HOST)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.asked.append(str(request.url))
        path = request.url.path
        if path == "/api/v1/security/init":
            return httpx.Response(200, json={"code": 1, "data": {"session_id": "sid", "expires_in": 600}})
        if path == "/api/v1/video/detail":
            card = self.cards.get(int(request.url.params["id"]))
            if card is None:
                return httpx.Response(200, json={"code": 0, "msg": "视频不存在"})
            return httpx.Response(200, json={"code": 1, "data": {"vod_info": {
                "vod_name": card.name, "vod_year": "2021", "vod_url_with_player": [
                    {"code": card.line, "name": card.line, "url": "#".join(f"{label}${token}" for label, token in card.items)}]}}})
        if path == "/api/v1/search":
            return httpx.Response(200, json={"code": 1, "data": {"total": len(self.cards), "total_pages": 1, "hits": [
                {"vod_id": card.number, "vod_name": card.name, "vod_year": "2021"} for card in self.cards.values()]}})
        if path == "/api/v1/vod/ticket":
            claims = {"data": {"parse_api": "/api/v1/vod/parse?url=", "url": json.loads(request.content)["token"]}}
            ticket = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=") + ".signature"
            return httpx.Response(200, json={"code": 1, "data": {"ticket": ticket}})
        if path == "/api/v1/vod/parse":
            return httpx.Response(200, json={"code": 200, "url": self.streams[dict(parse_qsl(request.url.query.decode()))["url"]]})
        return httpx.Response(404)


# --- AppV7 (小猪): a request sealed by AES-CBC under the key of the build, a card whose line names its parser ---------------

class AppV7Model(_Model):
    API, PARSER = "https://appv7.model/v7/logs", "https://appv7-parser.model/p?url="
    BUILD = {"package": "pk", "build_time": "1700000000", "salt": "salt"}

    def profile(self) -> AppProfile:
        return AppProfile("xiaozhu", "小猪", "appv7", {
            "label": "小猪", "protocol": "appv7", "api": self.API, "version": "1.0", "version_number": "1",
            "platform_version": "13", "card_identity": json.dumps({"app": "model"}), **self.BUILD})

    def owns(self, url: str) -> bool:
        return url.startswith((self.API, self.PARSER))

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.asked.append(str(request.url))
        if request.method == "GET":
            # The parser is asked with the token the secret method sealed.
            return httpx.Response(200, json={"code": 200, "url": self.streams[request.url.params["url"].removeprefix("sealed-")]})
        wire = base64.b64decode(request.content)
        body = json.loads(cbc(wire[16:], api_key(self.BUILD), wire[:16], decrypt=True))
        method, data = body["method"], body["data"]
        if method == "video_detail":
            card = self.cards.get(data["id"])
            if card is None:
                return httpx.Response(200, json={"code": 0, "msg": "视频不存在", "data": None})
            return httpx.Response(200, json={"code": 1, "data": {"vod_info": {
                "vod_id": card.number, "vod_name": card.name, "vod_year": "2021", "vod_url_with_player": [{
                    "code": card.line, "name": card.line, "parse_api": self.PARSER, "parse_secret": True,
                    "url": "#".join(f"{label}${token}" for label, token in card.items)}]}}})
        if method == "search":
            return httpx.Response(200, json={"code": 1, "data": [
                {"vod_id": card.number, "vod_name": card.name, "vod_year": "2021"} for card in self.cards.values()]})
        if method == "secret_parse_api_url":
            return httpx.Response(200, json={"code": 1, "data": "sealed-" + data["url"]})
        return httpx.Response(404)


# --- 139 Cloud: the public shares of the profile, the files of a folder, the preview stream of a file ---------------------------

class Share139Model(_Model):
    """The cloud: a share per card (`link<number>`), its folder the files of the card — a label and a token each — and the
    address of the preview of a file. `widths` gives the width the cloud stores for a token (3840 when it gives none)."""
    NUMBERS = range(500, 520)

    def __init__(self) -> None:
        super().__init__()
        self.widths: dict[str, int] = {}
        self.listings = 0

    def profile(self) -> AppProfile:
        shares = {str(number): {"title": f"万界独尊{number}", "year": "2021", "link_id": f"link{number}", "folder_id": "root"}
                  for number in self.NUMBERS}
        return AppProfile("share139", "139云盘", "share139", {"label": "139云盘", "protocol": "share139", "shares": json.dumps(shares)})

    def owns(self, url: str) -> bool:
        return url.startswith("https://share-kd-njs.yun.139.com/")

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.asked.append(str(request.url))
        method, body = request.url.path.rsplit("/", 1)[-1], json.loads(request.content)
        if method == "getOutLinkInfoV6":
            self.listings += 1
            card = self.cards.get(int(body["getOutLinkInfoReq"]["linkID"].removeprefix("link")))
            files = [{"coID": token, "coName": f"{label}.mp4", "coSize": 1,
                      "extInfo": {"width": str(self.widths.get(token, 3840)), "height": "1640"}}
                     for label, token in (card.items if card else [])]
            return httpx.Response(200, json={"code": "0", "data": {"coLst": files, "caLst": []}})
        if method == "getContentInfoFromOutLink":
            token = body["getContentInfoFromOutLinkReq"]["contentId"]
            return httpx.Response(200, json={"code": "0", "data": {"contentInfo": {"presentURL": self.streams[token]}}})
        return httpx.Response(404)
