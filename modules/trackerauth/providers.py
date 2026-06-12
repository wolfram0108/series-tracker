"""Провайдеры трекеров: логин-флоу, детект протухания, заголовки.

Вся трекеро-специфика — здесь (решение Р-1): потребители видят только
query `trackerauth.fetch`. Каждый провайдер отвечает на три вопроса:
как войти, как понять по ответу, что нас разлогинило, и какие
заголовки нужны запросу.

Маркеры выбраны детерминированными (вместо гадания по подстрокам
«login/error/пароль» из старой системы — см. разбор auth.py):
- Kinozal: разлогиненному показывают форму входа action=".../takelogin.php"
  (на залогиненных страницах её нет вовсе); успех входа = редирект НЕ на
  takelogin.php. Маркер устойчив к ведущему слешу и кавычкам формы
  (находка 42).
- RuTracker: признак живой сессии — кука `bb_session`; она появляется
  только после успешного логина и пропадает с его смертью.
- Astar: логина нет, есть Cloudflare — сессию делает cloudscraper.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

# Форма входа Kinozal: action может быть со слешем и в любых кавычках.
_LOGIN_FORM_RE = re.compile(r"""action=['"]?/?takelogin\.php""", re.I)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


class TrackerLoginError(RuntimeError):
    """Логин не удался по содержательной причине (учётки и т.п.)."""


def _base(url: str, strip_prefixes: tuple[str, ...] = ("www.",)) -> tuple[str, str]:
    p = urlparse(url)
    domain = p.netloc
    for prefix in strip_prefixes:
        domain = domain.removeprefix(prefix)
    return domain, f"{p.scheme}://{domain}"


class KinozalProvider:
    service = "kinozal"

    def normalize_domain(self, url: str) -> str:
        # dl.kinozal.me (скачивание) живёт на той же сессии, что kinozal.me
        return _base(url, ("dl.", "www."))[0]

    def login(self, session: requests.Session, credentials: dict,
              url: str) -> None:
        domain, base = _base(url, ("dl.", "www."))
        resp = session.post(
            f"{base}/takelogin.php",
            data={"username": credentials["username"],
                  "password": credentials["password"], "returnto": ""},
            headers={"User-Agent": UA}, timeout=15)
        resp.raise_for_status()
        if "takelogin.php" in resp.url:
            raise TrackerLoginError(f"Kinozal ({domain}) не принял логин — "
                                    "проверьте учётные данные")

    def is_logged_out(self, resp: requests.Response) -> bool:
        # форма входа: action="/takelogin.php" | "takelogin.php" | '...'
        # (на залогиненных страницах формы нет — находка 42)
        if "takelogin.php" in resp.url:
            return True
        return bool(_LOGIN_FORM_RE.search(resp.text[:20000]))

    def request_headers(self, url: str) -> dict:
        # Скачивание с dl.* требует Referer основного домена (из разбора
        # старого qbittorrent.py — подтверждено как необходимость).
        _, base = _base(url, ("dl.", "www."))
        return {"User-Agent": UA, "Referer": f"{base}/"}


class RutrackerProvider:
    service = "rutracker"

    def normalize_domain(self, url: str) -> str:
        return _base(url)[0]

    def login(self, session: requests.Session, credentials: dict,
              url: str) -> None:
        domain, base = _base(url)
        login_url = f"{base}/forum/login.php"
        page = session.get(login_url, headers={"User-Agent": UA}, timeout=15)
        form_data = {}
        form = BeautifulSoup(page.text, "html.parser").find(
            "form", {"method": "post"})
        if form:  # скрытые поля формы (анти-CSRF и пр.)
            for field in form.find_all("input"):
                name = field.get("name")
                if name and name not in ("login_username", "login_password"):
                    form_data[name] = field.get("value", "")
        form_data["login_username"] = credentials["username"]
        form_data["login_password"] = credentials["password"]
        form_data.setdefault("login", "Вход")
        resp = session.post(login_url, data=form_data, timeout=15, headers={
            "User-Agent": UA, "Referer": login_url})
        resp.raise_for_status()
        if "bb_session" not in session.cookies.get_dict():
            raise TrackerLoginError(f"RuTracker ({domain}) не выдал "
                                    "bb_session — логин не принят")

    def is_logged_out(self, resp: requests.Response) -> bool:
        jar = getattr(resp, "cookies", None)
        # bb_session живёт в сессии; на самом ответе её может не быть —
        # надёжнее признак формы логина в теле.
        return "login.php" in resp.url or (
            'name="login_username"' in resp.text[:20000])

    def request_headers(self, url: str) -> dict:
        headers = {"User-Agent": UA}
        if "/forum/dl.php" in url:  # скачивание .torrent
            import re
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
    """Логина нет; Cloudflare проходится cloudscraper'ом."""
    service = "astar"

    def normalize_domain(self, url: str) -> str:
        return _base(url)[0]

    def make_session(self) -> requests.Session:
        import cloudscraper
        return cloudscraper.create_scraper()

    def login(self, session: requests.Session, credentials: dict,
              url: str) -> None:
        pass  # вход не требуется

    def is_logged_out(self, resp: requests.Response) -> bool:
        return False  # сессии нет — протухать нечему (CF решает scraper)

    def request_headers(self, url: str) -> dict:
        return {}


class VkProvider:
    """VK API: авторизация токеном в параметрах запроса; сессии и логина
    нет — токен статичен (хранится в auth: auth_type='vk', поле password,
    как в старой системе). Токен не покидает trackerauth."""
    service = "vk"
    needs_credential_params = True

    def normalize_domain(self, url: str) -> str:
        return "api.vk.com"

    def login(self, session: requests.Session, credentials: dict,
              url: str) -> None:
        raise TrackerLoginError(
            "VK: токен статичен, релогин невозможен — проверьте Access Token")

    def is_logged_out(self, resp: requests.Response) -> bool:
        return False  # ошибки API приходят JSON-ом, их разбирает sources

    def request_headers(self, url: str) -> dict:
        return {"User-Agent": UA}

    def credential_params(self, credentials: dict) -> dict:
        return {"access_token": credentials["password"], "v": "5.199"}


PROVIDERS = {p.service: p for p in (KinozalProvider(), RutrackerProvider(),
                                    AstarProvider(), VkProvider())}
