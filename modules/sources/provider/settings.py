"""What the tab of the provider of external sources may write: its address and its token (settings `provider.url`,
`provider.token`; the environment `ST_PROVIDER_URL` and `ST_PROVIDER_TOKEN` stand while no address is stored, so a stored
empty address is a deliberate «none»). Pure functions: the module carries their results across the bus; a save is checked
whole and refused with coded problems, a refused value is never written."""
from __future__ import annotations

from urllib.parse import urlsplit

from core.contracts import ProviderSettingsDraft, Reason, SettingProblem
from core.reasons import parse as parse_reason, reason

URL_KEY = "provider.url"
TOKEN_KEY = "provider.token"


def clean(draft: ProviderSettingsDraft) -> ProviderSettingsDraft:
    """Address trimmed; a token is kept as typed (`None`: the stored one stays)."""
    return ProviderSettingsDraft(url=draft.url.strip().rstrip("/"), token=draft.token)


def check(draft: ProviderSettingsDraft) -> list[SettingProblem]:
    """What is wrong with the draft once it is cleaned: an address is an http or https one with a host."""
    draft = clean(draft)
    if not draft.url:
        return []
    found = urlsplit(draft.url)
    if found.scheme not in ("http", "https") or not found.hostname or " " in draft.url:
        return [SettingProblem(field="url", detail=Reason.model_validate(parse_reason(reason("provider_url_invalid", url=draft.url))))]
    return []


def stored(draft: ProviderSettingsDraft) -> dict[str, str]:
    """The settings to write for a cleaned draft: the token only when the draft names one."""
    return {URL_KEY: draft.url, **({} if draft.token is None else {TOKEN_KEY: draft.token})}
