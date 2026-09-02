"""Прохождение антибот-челленджа трекера настоящим браузером.

Зачем это здесь. Часть трекеров закрыта Cloudflare в режиме managed
challenge: сервер отвечает 403 с заголовком `cf-mitigated: challenge`
и страницей-заглушкой «Один момент…», а настоящую страницу отдаёт лишь
после того, как браузер выполнит JS-проверку. Проверка проверяет именно
клиента, а не адрес: обычный браузер из этой же сети сайт открывает.

Что НЕ работает (проверено на живом rutracker, зафиксировано в
contracts/findings.md, находка 64):

| Способ                              | Результат          |
|-------------------------------------|--------------------|
| requests с браузерным User-Agent    | 403, челлендж      |
| cloudscraper                        | 403, челлендж      |
| curl_cffi (подмена TLS-отпечатка)   | 403, челлендж      |
| Playwright Firefox/Chromium         | челлендж не решён  |
| camoufox (антидетект-сборка Firefox)| 200 за ~6 секунд   |

Отсюда выбор: челлендж не обходится подделкой отпечатка — его нужно
РЕШАТЬ, и решает его браузер, которого защита не опознаёт как
автоматизированный (camoufox — сборка Firefox с патчами против детекта).

Результат прохождения — кука `cf_clearance` плюс User-Agent, которым она
получена: кука действительна только в паре с этим UA. Дальше обычная
requests-сессия ходит на трекер сама, без браузера; браузер поднимается
только когда защита снова показала челлендж (см. `module.on_fetch`).

Признак успеха выбран детерминированным — появление куки `cf_clearance`,
а не подстрока в заголовке страницы («Один момент…»/«Just a moment…»
зависят от языка и вёрстки).

Распознаётся челлендж здесь же и одинаково для всех трекеров: маркер —
служебный заголовок `cf-mitigated: challenge`, который ставит сам
Cloudflare. Защита включается на стороне сайта в любой момент и трекеру
не принадлежит, поэтому и умение её заметить не может быть свойством
отдельного провайдера.

`arm()` вешает распознавание на сессию: любой её запрос, получивший
заглушку, поднимает `ChallengeSeen` вместо того чтобы вернуть её как
ответ. Так «заглушку приняли за страницу» становится непредставимым
состоянием — включая пути, которые ходят мимо общей обёртки запроса
(логин провайдера).
"""
from __future__ import annotations

import asyncio
from urllib.parse import urlparse

import requests

CLEARANCE_COOKIE = "cf_clearance"
CHALLENGE_HEADER = "cf-mitigated"

# На живом rutracker челлендж решается за ~6 с плюс старт браузера; 45 —
# запас в разы. Верхняя граница не произвольна: она должна укладываться в
# бюджет ожидания у потребителя (sources ждёт trackerauth.fetch), иначе
# запрос отвалится по шине раньше, чем браузер закончит работу.
SOLVE_TIMEOUT = 45.0
POLL_INTERVAL = 1.0


class ChallengeError(RuntimeError):
    """Челлендж не пройден: браузер не получил `cf_clearance` в срок."""


class ChallengeSeen(RuntimeError):
    """Ответ оказался заглушкой антибот-защиты, а не страницей."""

    def __init__(self, url: str) -> None:
        super().__init__(f"антибот-челлендж на {url}")
        self.url = url


def is_challenge(resp: requests.Response) -> bool:
    """Заглушка защиты — по заголовку самого Cloudflare, а не по телу:
    текст «Один момент…» зависит от языка ответа, заголовок — нет."""
    return resp.headers.get(CHALLENGE_HEADER, "").lower() == "challenge"


def arm(session: requests.Session) -> None:
    """Научить сессию не выдавать заглушку защиты за ответ."""
    session.hooks["response"].append(_raise_on_challenge)


def _raise_on_challenge(resp: requests.Response, *args, **kwargs) -> None:
    if is_challenge(resp):
        raise ChallengeSeen(resp.url)


async def solve(url: str, *, timeout: float = SOLVE_TIMEOUT
                ) -> tuple[str, dict[str, str]]:
    """Пройти челлендж на `url` браузером.

    Возвращает `(user_agent, cookies)` — куки домена страницы вместе с
    `cf_clearance` и UA, которым они получены (использовать их можно
    только вместе). Бросает `ChallengeError`, если куки не появились.
    """
    from camoufox.async_api import AsyncCamoufox

    domain = urlparse(url).netloc
    deadline = asyncio.get_running_loop().time() + timeout
    async with AsyncCamoufox(headless=True, humanize=True,
                             locale="ru-RU", os="windows") as browser:
        page = await browser.new_page()
        # Челлендж отдаётся с кодом 403 — это ожидаемый ответ, не ошибка:
        # страница сама себя заменит после выполнения проверки.
        await page.goto(url, timeout=int(timeout * 1000),
                        wait_until="domcontentloaded")
        while asyncio.get_running_loop().time() < deadline:
            cookies = {c["name"]: c["value"]
                       for c in await page.context.cookies()
                       if c.get("domain", "").lstrip(".") in domain}
            if CLEARANCE_COOKIE in cookies:
                user_agent = await page.evaluate("navigator.userAgent")
                return user_agent, cookies
            await page.wait_for_timeout(int(POLL_INTERVAL * 1000))
    raise ChallengeError(
        f"челлендж на {domain} не пройден за {timeout:.0f} с: "
        f"куки {CLEARANCE_COOKIE} нет")
