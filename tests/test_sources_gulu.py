"""The application 咕噜 (modules/sources/apps/gulu.py): the derivation of the key and the sealing of a request on
vectors made by another library (the owner's prototype), and the client against a model of the server by its protocol —
the handshake, the session, the boot with its players and parsers, the card, the stream of an episode, the search."""
from __future__ import annotations

import base64
import zlib

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from modules.sources.apps import AppError, AppProfile
from modules.sources.apps.appdrama import field, fields
from modules.sources.apps.gulu import GuluClient, cbc, first, message, opened, packed, sealed, session_key

ENDPOINT = "https://gulu.model/app/v2"
PARSER_A, PARSER_B, PARSER_C = "https://a.model/api?key=k&url=", "https://b.model/tos.php?url=", "https://c.model/api?url="


def test_the_key_of_a_session_and_a_sealed_request_are_what_another_library_makes_of_the_same_secret():
    key = session_key(bytes(range(32)), "session-42")
    assert key.hex() == "16bde40953ee2691bdc5adcfd3c498694ff7b94a96662dddbfb28eaefabf0ce4"
    wire = bytes.fromhex("000102030405060708090a0bc1218f19c019da59aeca41ada8882a4109932482e3236bea35579406c0c7e542")
    assert opened(key, wire) == b"hello gulu " * 4 and opened(key, sealed(key, b"hello gulu " * 4, bytes(range(12)))) == b"hello gulu " * 4
    assert packed(bytes([38, 40, 0x80, 0x01])) == [38, 40, 128]


def player(number: int, name: str, code: str, parsers: list[int]) -> bytes:
    return message((1, number), (2, name), (3, code), (4, name), (8, bytes(parsers)))


def parser(number: int, name: str, address: str, key: str = "url") -> bytes:
    return message((1, number), (2, name), (3, address), (4, "json"), (10, key))


BOOT = (field(4, player(125, "新咕噜4K", "newxfyun", [38])) + field(4, player(7, "橘汁TOS", "tos", [39]))
        + field(4, player(9, "咖啡", "covip", [22, 38])) + field(7, parser(38, "新咕噜4K", PARSER_A))
        + field(7, parser(39, "橘汁TOS", PARSER_B, "main_url")) + field(7, parser(22, "咖啡", PARSER_C)))


def line(code: str, episodes: list[tuple[str, str]]) -> bytes:
    return message((1, code), *((2, message((3, token), (4, label))) for label, token in episodes))


DETAIL = (message((1, 776), (5, "师兄啊师兄"))
          + field(75, line("newxfyun", [("第01集", "xfy-1"), ("第02集", "xfy-2")]))
          + field(75, line("lzm3u8", [("第01集", "https://cdn.model/l/1/index.m3u8")]))
          + field(75, line("covip", [("1", "covip_1")])) + field(75, line("tos", [("1", "tos_1")])))
FOUND = field(1, field(3, message((1, 776), (2, 2023), (3, "师兄啊师兄")))) + field(1, field(3, message((1, 1291), (2, 2021), (3, "万界独尊"))))


class Server:
    """The server of the application: it agrees a key with whoever shakes hands, and answers under it."""

    def __init__(self) -> None:
        self.keys: dict[str, bytes] = {}
        self.asked: list[tuple[int, int]] = []
        self.parsed: list[str] = []
        self.forget_after: int | None = None  # the session is dropped after this many requests

    def handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if not url.startswith(ENDPOINT):
            self.parsed.append(url)
            token = request.url.params["url"]
            if url.startswith(PARSER_A):
                return httpx.Response(200, json={"code": 200, "url": f"https://cdn.model/x/{token}.m3u8"})
            if url.startswith(PARSER_B):
                return httpx.Response(200, json={"main_url": f"https://tos.model/{token}.mp4"})
            return httpx.Response(200, json={"code": 403, "msg": "访问受限：IP 未授权"})
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
        session = request.headers["X-Session-Id"]
        if self.forget_after is not None and len(self.asked) >= self.forget_after:
            self.forget_after = None
            self.keys.pop(session, None)
        key = self.keys.get(session)
        if key is None:
            return httpx.Response(200, content=message((1, 0), (3, "session expired")))
        plain = opened(key, first(request.content, 2))
        method = (first(plain, 2), first(plain, 3))
        self.asked.append(method)
        answers = {(1, 0): BOOT, (3, 62): DETAIL, (3, 61): FOUND}
        body = field(4, answers[method]) if method in answers else field(3, "no such request")
        return httpx.Response(200, content=message((1, 0), (2, sealed(key, body, bytes(range(12))))))


@pytest.fixture
async def world():
    server = Server()
    async with httpx.AsyncClient(transport=httpx.MockTransport(server.handle)) as http:
        yield server, GuluClient(AppProfile("gulu", "Gulu", "gulu", {"endpoint": ENDPOINT}), lambda: http)


async def test_the_card_lists_its_lines_with_the_names_and_the_parsers_the_boot_gives_them(world):
    server, client = world
    card = await client.card("776")
    assert card.names == ["师兄啊师兄"]
    assert [(each.code, each.name, each.listed, each.parsers, [(item.label, item.token) for item in each.items])
            for each in card.lines] == [
        ("newxfyun", "新咕噜4K", False, "38", [("第01集", "xfy-1"), ("第02集", "xfy-2")]),
        ("lzm3u8", "", True, "", [("第01集", "https://cdn.model/l/1/index.m3u8")]),
        ("covip", "咖啡", False, "22,38", [("1", "covip_1")]), ("tos", "橘汁TOS", False, "39", [("1", "tos_1")])]
    # One handshake and one boot serve every request of the session.
    assert server.asked == [(1, 0), (3, 62)] and len(server.keys) == 1
    await client.card("776")
    assert server.asked == [(1, 0), (3, 62), (3, 62)] and len(server.keys) == 1


async def test_the_stream_of_an_episode_is_its_address_or_what_the_parsers_of_its_line_answer(world):
    server, client = world
    # A line without a parser lists its playlists itself.
    assert (await client.resolve("lzm3u8", "https://cdn.model/l/1/index.m3u8")).address == "https://cdn.model/l/1/index.m3u8"
    assert server.parsed == []
    # The parser of the line, the token appended to its address; the stream is under the key the parser is given.
    assert (await client.resolve("newxfyun", "xfy-1")).address == "https://cdn.model/x/xfy-1.m3u8"
    assert (await client.resolve("tos", "tos_1")).address == "https://tos.model/tos_1.mp4"
    # The parsers of a line in their order: the first refuses in words, the next gives the stream.
    assert (await client.resolve("covip", "covip/1")).address == "https://cdn.model/x/covip/1.m3u8"
    assert server.parsed[-2:] == [PARSER_C + "covip%2F1", PARSER_A + "covip%2F1"]


async def test_a_refusal_of_every_parser_of_a_line_is_the_answer_about_the_episode_in_the_words_of_the_last(world):
    server, client = world
    await client.card("776")
    client._boot = field(4, player(9, "咖啡", "covip", [22])) + field(7, parser(22, "咖啡", PARSER_C))
    with pytest.raises(AppError) as refused:
        await client.resolve("covip", "covip_1")
    assert "访问受限" in refused.value.reason and not refused.value.transient
    with pytest.raises(AppError) as unknown:
        await client.resolve("nowhere", "token")
    assert "app_no_parser" in unknown.value.reason


async def test_a_session_the_server_dropped_is_opened_anew_once_and_the_request_is_asked_again(world):
    server, client = world
    await client.card("776")
    server.forget_after = len(server.asked)
    card = await client.card("776")
    assert card.names == ["师兄啊师兄"] and len(server.keys) == 1 and server.asked[-2:] == [(1, 0), (3, 62)]


async def test_the_search_gives_the_cards_with_their_numbers_years_and_names(world):
    _server, client = world
    page = await client.search_page("师兄啊师兄", 1)
    assert [(card["id"], card["name"], card["year"]) for card in page.cards] == [(776, "师兄啊师兄", "2023"), (1291, "万界独尊", "2021")]


def test_the_fields_of_a_protobuf_read_back_what_was_written():
    assert fields(message((1, 7), (2, "текст"), (3, b"\x00\x01"))) == [(1, 7), (2, "текст".encode()), (3, b"\x00\x01")]
