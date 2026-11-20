"""What an address is, and when the provider of the socket is to be asked about it.

A page of the provider's world is one a site of its manifest reads, or an address that is no web address and no site reads: such a page is
read and downloaded by the provider alone. While the provider is not up nothing of that world is begun, and the one word the tracker answers
with is the reason below.
"""
from __future__ import annotations

from urllib.parse import urlsplit

from core.reasons import reason

NOT_CONNECTED = "provider_not_connected"
WEB_SCHEMES = ("http", "https")


def is_web_address(url: str) -> bool:
    return urlsplit(url).scheme.lower() in WEB_SCHEMES


def not_connected(state: str | None, incompatible: str = "") -> str | None:
    """The reason the provider cannot be asked now (a code of core/reasons); None when it is up. `state` is that of the socket (None: no
    provider is set), `incompatible` the reason a provider of another protocol was told with."""
    if state is None:
        return reason(NOT_CONNECTED, state="none")
    if state == "up":
        return None
    if state == "incompatible" and incompatible:
        return incompatible
    return reason(NOT_CONNECTED, state=state)
