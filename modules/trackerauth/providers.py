"""Tracker providers: login flow, expiry detection, headers.

All tracker-specific logic lives here (decision Р-1): consumers see only the
`trackerauth.fetch` query. Each provider answers four questions: how to log
in, how to tell from a response that we were logged out, which headers a
request needs, and (optionally) how to recognise an anti-bot challenge.

A challenge is a separate state, not to be confused with a logout: the session
can be alive while the site's protection does not let the client in at all.
The transport recognises it (`cf_challenge.arm` on the session), the same way
for all trackers; a provider declares only `challenge_url` if the challenge
must be passed at a URL other than the one requested.

`user_agent` is the session's identity: the provider declares it as data, the
module sets it on the session, and all requests (including login) go out under
one UA. Otherwise the anti-bot clearance, valid only together with its own UA,
breaks on the first request built outside the common point.

The markers are chosen to be deterministic (instead of guessing by substrings
«login/error/пароль» from the old system, see the analysis of auth.py):
- Kinozal: a logged-out user is shown a login form with action=".../takelogin.php"
  (logged-in pages have no such form at all); a successful login is a redirect
  NOT to takelogin.php. The marker holds up to a leading slash and the form's
  quotes (finding 42).
- RuTracker: the sign of a live session is the `bb_session` cookie; it appears
  only after a successful login and vanishes when the session dies.
- Astar: no login, but Cloudflare is present; the session is made by cloudscraper.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from core.reasons import reason

# Kinozal login form: action may carry a leading slash and any quote style.
_LOGIN_FORM_RE = re.compile(r"""action=['"]?/?takelogin\.php""", re.I)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


class TrackerLoginError(RuntimeError):
    """Login failed for a substantive reason (bad credentials and the like)."""


def _base(url: str, strip_prefixes: tuple[str, ...] = ("www.",)) -> tuple[str, str]:
    p = urlparse(url)
    domain = p.netloc
    for prefix in strip_prefixes:
        domain = domain.removeprefix(prefix)
    return domain, f"{p.scheme}://{domain}"


class KinozalProvider:
    service = "kinozal"
    user_agent = UA

    def normalize_domain(self, url: str) -> str:
        # dl.kinozal.me (downloads) shares the session with kinozal.me
        return _base(url, ("dl.", "www."))[0]

    def login(self, session: requests.Session, credentials: dict,
              url: str) -> None:
        domain, base = _base(url, ("dl.", "www."))
        resp = session.post(
            f"{base}/takelogin.php",
            data={"username": credentials["username"],
                  "password": credentials["password"], "returnto": ""},
            timeout=15)
        resp.raise_for_status()
        if "takelogin.php" in resp.url:
            raise TrackerLoginError(reason("kinozal_login_rejected", domain=domain))

    def is_logged_out(self, resp: requests.Response) -> bool:
        # login form: action="/takelogin.php" | "takelogin.php" | '...'
        # (logged-in pages have no such form, finding 42)
        if "takelogin.php" in resp.url:
            return True
        return bool(_LOGIN_FORM_RE.search(resp.text[:20000]))

    def request_headers(self, url: str) -> dict:
        # Downloads from dl.* require the main domain as Referer (from the analysis
        # of the old qbittorrent.py; confirmed as necessary).
        _, base = _base(url, ("dl.", "www."))
        return {"Referer": f"{base}/"}


class RutrackerProvider:
    service = "rutracker"
    user_agent = UA

    def normalize_domain(self, url: str) -> str:
        return _base(url)[0]

    def login(self, session: requests.Session, credentials: dict,
              url: str) -> None:
        domain, base = _base(url)
        login_url = f"{base}/forum/login.php"
        page = session.get(login_url, timeout=15)
        form_data = {}
        form = BeautifulSoup(page.text, "html.parser").find(
            "form", {"method": "post"})
        if form:  # hidden form fields (anti-CSRF and the like)
            for field in form.find_all("input"):
                name = field.get("name")
                if name and name not in ("login_username", "login_password"):
                    form_data[name] = field.get("value", "")
        form_data["login_username"] = credentials["username"]
        form_data["login_password"] = credentials["password"]
        form_data.setdefault("login", "Вход")
        resp = session.post(login_url, data=form_data, timeout=15,
                            headers={"Referer": login_url})
        resp.raise_for_status()
        if "bb_session" not in session.cookies.get_dict():
            # RuTracker puts a captcha into the login form (hidden cap_sid and input
            # cap_code_<hash>). It cannot be solved automatically, so say so instead
            # of a generic "login rejected": the cause and remedy differ (checking the
            # password and logging in by hand are different fixes).
            if "cap_sid" in page.text or "cap_code" in page.text:
                raise TrackerLoginError(reason("rutracker_captcha", domain=domain))
            raise TrackerLoginError(reason("rutracker_login_rejected", domain=domain))

    def is_logged_out(self, resp: requests.Response) -> bool:
        jar = getattr(resp, "cookies", None)
        # bb_session lives in the session and may be absent from the response
        # itself, so the login form in the body is the more reliable sign.
        return "login.php" in resp.url or (
            'name="login_username"' in resp.text[:20000])

    def challenge_url(self, url: str) -> str:
        """Where to pass the challenge for a request to `url`.

        dl.php returns a .torrent file, which makes no sense to open in a browser
        (the challenge cannot be shown there). Clearance is shared per domain, so
        we pass it on the topic page of the same release.
        """
        if "/forum/dl.php" in url:
            return url.replace("/forum/dl.php", "/forum/viewtopic.php")
        return url

    def request_headers(self, url: str) -> dict:
        headers: dict = {}
        if "/forum/dl.php" in url:  # .torrent download
            match = re.search(r"t=(\d+)", url)
            _, base = _base(url)
            headers.update({
                "Referer": (f"{base}/forum/viewtopic.php?t={match.group(1)}"
                            if match else url),
                "Origin": base,
                "X-Requested-With": "XMLHttpRequest",
            })
        return headers


class AstarProvider:
    """No login; Cloudflare is passed by cloudscraper."""
    service = "astar"
    user_agent = None  # cloudscraper picks the UA for its own bypass itself

    def normalize_domain(self, url: str) -> str:
        return _base(url)[0]

    def make_session(self) -> requests.Session:
        import cloudscraper
        return cloudscraper.create_scraper()

    def login(self, session: requests.Session, credentials: dict,
              url: str) -> None:
        pass  # no login required

    def is_logged_out(self, resp: requests.Response) -> bool:
        return False  # no session, so nothing to expire (CF is handled by the scraper)

    def request_headers(self, url: str) -> dict:
        return {}


class VkProvider:
    """VK API: auth by token in the request parameters; there is no session or
    login, the token is static (stored in auth: auth_type='vk', the password
    field, as in the old system). The token never leaves trackerauth."""
    service = "vk"
    needs_credential_params = True
    # With a browser UA the VK API returns video without the files field (direct
    # mp4 and resolutions are dropped), so requests' default UA stays (finding 44).
    user_agent = None

    def normalize_domain(self, url: str) -> str:
        return "api.vk.com"

    def login(self, session: requests.Session, credentials: dict,
              url: str) -> None:
        raise TrackerLoginError(reason("vk_token_static"))

    def is_logged_out(self, resp: requests.Response) -> bool:
        return False  # API errors arrive as JSON, and sources parses them

    def request_headers(self, url: str) -> dict:
        return {}

    def credential_params(self, credentials: dict) -> dict:
        return {"access_token": credentials["password"], "v": "5.199"}


PROVIDERS = {p.service: p for p in (KinozalProvider(), RutrackerProvider(),
                                    AstarProvider(), VkProvider())}
