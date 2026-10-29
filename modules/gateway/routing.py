"""Route registration helpers shared by the gateway API blocks."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter


def route_per_method(router: APIRouter, path: str, *, methods: list[str], **options: Any) -> Callable:
    # OpenAPI gives a route one operation id for all its methods, so a shared route repeats it
    # and the id picked for the generated types depends on set order.
    def register(handler: Callable) -> Callable:
        for method in methods:
            router.add_api_route(path, handler, methods=[method], **options)
        return handler

    return register
