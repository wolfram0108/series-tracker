"""Route registration and answer helpers shared by the gateway API blocks."""
from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from core import NO_HANDLER, BusRequestError
from core.admission import Admission
from core.admission import current as current_admission



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
                  else 400 if message.startswith("ValueError") else 503 if message.startswith(NO_HANDLER) else 500)
    text = message.split(": ", 1)[-1]
    body = {"success": False, "error": text} if with_success else {"error": text}
    return JSONResponse(body, status_code=status)


@asynccontextmanager
async def admitted(gw: Any, series_id: int) -> AsyncIterator[None]:
    """A change of a series by the user, admitted by the owner of its runs (scan.edit.begin): refused while a sync of the
    series runs, and a sync does not open until the change is written whole, however many modules it writes to (И-60).
    The admission lasts until every request of the change is answered, not until this block stops waiting: a fuse or a
    dropped HTTP request leaves a write in the hands of its handler (core/admission.py). The refusal is a BusRequestError
    like any other, answered by `refusal`."""
    try:
        reply = await gw.request("scan.edit.begin", {"series_id": series_id})
    except BusRequestError as exc:
        if NO_HANDLER not in str(exc):
            raise
        yield  # no runs in this process: nothing to hold off
        return
    token = (reply or {}).get("token") or ""
    admission = Admission(token, lambda: gw.send_command("scan.edit.end", {"series_id": series_id, "token": token}),
                          log=getattr(gw, "log", None))
    current = current_admission.set(admission)
    try:
        yield
    finally:
        current_admission.reset(current)
        admission.close()
