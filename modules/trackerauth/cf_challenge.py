"""Passing the tracker's anti-bot challenge with a real browser.

Why it lives here. Some trackers sit behind Cloudflare in managed challenge
mode: the server answers 403 with the header `cf-mitigated: challenge` and a
stub page «Один момент…», and serves the real page only after the browser
runs the JS check. The check targets the client, not the address: an ordinary
browser from the same network opens the site.

What does NOT work (checked on live rutracker, recorded in
contracts/findings.md, finding 64):

| Method                              | Result             |
|-------------------------------------|--------------------|
| requests with a browser User-Agent  | 403, challenge     |
| cloudscraper                        | 403, challenge     |
| curl_cffi (TLS fingerprint spoof)   | 403, challenge     |
| Playwright Firefox/Chromium         | challenge not solved |
| camoufox (anti-detect Firefox build)| 200 in ~6 seconds  |

Hence the choice: the challenge is not bypassed by spoofing the fingerprint,
it has to be SOLVED, and the browser that solves it is one the protection
does not recognise as automated (camoufox is a Firefox build with patches
against detection).

The result of passing is the `cf_clearance` cookie plus the User-Agent it was
obtained with: the cookie is valid only together with that UA. After that a
plain requests session talks to the tracker itself, without a browser; the
browser is started only when the protection shows the challenge again (see
`module.on_fetch`).

The success signal is deterministic: the appearance of the `cf_clearance`
cookie, not a substring in the page title («Один момент…» / «Just a moment…»
depend on language and markup).

The challenge is recognised here, the same way for all trackers: the marker
is the service header `cf-mitigated: challenge`, set by Cloudflare itself.
The protection is switched on by the site at any moment and does not belong
to the tracker, so the ability to notice it cannot be a property of a single
provider.

`arm()` hooks recognition onto the session: any of its requests that gets the
stub raises `ChallengeSeen` instead of returning it as a response. This makes
"the stub was taken for a page" an unrepresentable state, including paths
that go around the common request wrapper (provider login).
"""
from __future__ import annotations

import asyncio
from urllib.parse import urlparse

import requests

from core.reasons import reason

CLEARANCE_COOKIE = "cf_clearance"
CHALLENGE_HEADER = "cf-mitigated"

# Live rutracker solves the challenge in ~6 s plus browser start, so 45 s is a
# wide margin; the cap must fit the consumer's wait (sources awaits trackerauth.fetch).
SOLVE_TIMEOUT = 45.0
POLL_INTERVAL = 1.0


class ChallengeError(RuntimeError):
    """Challenge not passed: the browser did not get `cf_clearance` in time."""


class ChallengeSeen(RuntimeError):
    """The response was an anti-bot protection stub, not a page."""

    def __init__(self, url: str) -> None:
        super().__init__(reason("challenge_page", url=url))
        self.url = url


def is_challenge(resp: requests.Response) -> bool:
    """The protection stub is detected by Cloudflare's own header, not the body:
    the «Один момент…» text depends on the response language, the header does not."""
    return resp.headers.get(CHALLENGE_HEADER, "").lower() == "challenge"


def arm(session: requests.Session) -> None:
    """Make the session never hand a protection stub back as a response."""
    session.hooks["response"].append(_raise_on_challenge)


def _raise_on_challenge(resp: requests.Response, *args, **kwargs) -> None:
    if is_challenge(resp):
        raise ChallengeSeen(resp.url)


async def solve(url: str, *, timeout: float = SOLVE_TIMEOUT
                ) -> tuple[str, dict[str, str]]:
    """Pass the challenge at `url` with a browser.

    Returns `(user_agent, cookies)`: the page domain's cookies including
    `cf_clearance`, and the UA they were obtained with (use them only together).
    Raises `ChallengeError` if the cookies never appear.
    """
    from camoufox.async_api import AsyncCamoufox

    domain = urlparse(url).netloc
    deadline = asyncio.get_running_loop().time() + timeout
    async with AsyncCamoufox(headless=True, humanize=True,
                             locale="ru-RU", os="windows") as browser:
        page = await browser.new_page()
        # The challenge comes back with 403 by design: the page replaces itself
        # once the check runs, so that status is expected, not an error.
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
    raise ChallengeError(reason("challenge_not_passed", domain=domain,
                                seconds=round(timeout), cookie=CLEARANCE_COOKIE))
