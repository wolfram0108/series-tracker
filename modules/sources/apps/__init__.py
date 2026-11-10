"""The video applications read through their own APIs: a title is found by a search in the application, its card lists the
lines of episodes, and the stream of an episode is asked of the application right before it is downloaded.

An application has no page a user could give: a source of it is the address `app://<application>/<card>` that its search
gives. The profile of an application (its host and what its client says of itself) is private and comes from the
environment of the process (`ST_APP_<NAME>`, JSON); without it the application is not read.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Mapping

SCHEME = "app"
ENV_PREFIX = "ST_APP_"


@dataclass(frozen=True)
class AppProfile:
    """What the client of an application says of itself, as its own build does, and the device it is known by."""
    name: str          # the name of the application in the addresses of its cards (`banri`)
    label: str         # its name as the application shows it (`半日`)
    protocol: str      # the family of its API (`app99`)
    settings: Mapping[str, str]


def profiles_of(environ: Mapping[str, str]) -> list[AppProfile]:
    """The applications the environment describes: `ST_APP_<NAME>` holds a JSON object with `label`, `protocol` and the
    settings of the protocol; a value that is no such object is a fault of the environment and stops the start."""
    found = []
    for key, value in sorted(environ.items()):
        if not key.startswith(ENV_PREFIX) or not value.strip():
            continue
        data = json.loads(value)
        if not isinstance(data, dict) or not data.get("protocol") or not data.get("label"):
            raise ValueError(f"{key}: an application needs a JSON object with `protocol` and `label`")
        found.append(AppProfile(name=key[len(ENV_PREFIX):].lower(), label=str(data["label"]),
                                protocol=str(data["protocol"]), settings={k: str(v) for k, v in data.items()}))
    return found


def card_address(app: str, card: str) -> str:
    return f"{SCHEME}://{app}/{card}"
