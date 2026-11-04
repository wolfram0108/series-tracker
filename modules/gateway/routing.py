"""Route registration and answer helpers shared by the gateway API blocks."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from core import BusRequestError

_NOT_THERE = "нет обработчика"


def route_per_method(router: APIRouter, path: str, *, methods: list[str], **options: Any) -> Callable:
    # OpenAPI gives a route one operation id for all its methods, so a shared route repeats it
    # and the id picked for the generated types depends on set order.
    def register(handler: Callable) -> Callable:
        for method in methods:
            router.add_api_route(path, handler, methods=[method], **options)
        return handler

    return register


def refusal(exc: BusRequestError, status: int | None = None, *, with_success: bool = True) -> JSONResponse:
    """A module's refusal as an HTTP answer: the reason the module gave (a code of core/reasons, worded by the
    interface) goes into `error` as it came. Without `status`: a missing entity is 404, an action the state of the object
    does not allow 409, a refused value 400, a module that is not there 503, anything else 500."""
    message = str(exc)
    if status is None:
        status = (404 if message.startswith("LookupError") else 409 if message.startswith("NotAllowed")
                  else 400 if message.startswith("ValueError") else 503 if message.startswith(_NOT_THERE) else 500)
    text = message.split(": ", 1)[-1]
    body = {"success": False, "error": text} if with_success else {"error": text}
    return JSONResponse(body, status_code=status)
