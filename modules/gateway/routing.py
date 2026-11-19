"""Route registration and answer helpers shared by the gateway API blocks."""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

import orjson
from fastapi import APIRouter
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response

from core import NO_HANDLER, BusRequestError, compute
from core.admission import Admission
from core.admission import current as current_admission
from core.reasons import code_of, relayed


def _text(answer: Any) -> bytes:
    # A value JSON has no form for (a date, a set) is written the way the framework writes it; keys that are numbers are
    # written as text, as JSON has it. Ten megabytes of a layout are written in tens of milliseconds, not in a hundred.
    return orjson.dumps(answer, default=jsonable_encoder, option=orjson.OPT_NON_STR_KEYS)


async def as_made(answer: Any) -> Response:
    """The answer of a module as it came. The module made it of the model of the route, so it is not checked and written
    again by the framework (four passes over megabytes on the loop); its text is made in the pool of computation."""
    return Response(await compute.run(_text, answer), media_type="application/json")


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
                  else 400 if message.startswith("ValueError") else 503 if code_of(message) == NO_HANDLER else 500)
    text = relayed(message)
    body = {"success": False, "error": text} if with_success else {"error": text}
    return JSONResponse(body, status_code=status)


@asynccontextmanager
async def admitted(gw: Any, series_id: int) -> AsyncIterator[None]:
    """A change of a series by the user, admitted by the owner of its runs (scan.edit.begin): refused while a sync of the
    series runs, and a sync does not open until the change is written whole, however many modules it writes to (И-60).
    The admission lasts until every request of the change is answered, not until this block stops waiting: a fuse or a
    dropped HTTP request leaves a write in the hands of its handler (core/admission.py). The refusal is a BusRequestError
    like any other, answered by `refusal`. The number of the change is made here, before it is granted: a caller that
    stops waiting for the grant (its fuse, a dropped request) gives the grant back by it, whether it was made or not."""
    token = uuid.uuid4().hex
    try:
        await gw.request("scan.edit.begin", {"series_id": series_id, "token": token})
    except BusRequestError as exc:
        if code_of(str(exc)) != NO_HANDLER:
            raise  # refused: the owner answered, nothing was granted
        yield  # no runs in this process: nothing to hold off
        return
    except BaseException:
        gw.send_command("scan.edit.end", {"series_id": series_id, "token": token})
        raise
    admission = Admission(token, lambda: gw.send_command("scan.edit.end", {"series_id": series_id, "token": token}),
                          log=getattr(gw, "log", None))
    current = current_admission.set(admission)
    try:
        yield
    finally:
        current_admission.reset(current)
        admission.close()
