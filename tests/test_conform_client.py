import json

import httpx
import pytest

from modules.conform.client import DaemonClient, accepted_fields

# The shape the daemon was seen to publish: the body of the route is a schema that forbids any other field.
SCHEMA = {
    "paths": {"/conform/enqueue": {"post": {"requestBody": {"content": {"application/json": {
        "schema": {"$ref": "#/components/schemas/ConformEnqueueBody"}}}}}}},
    "components": {"schemas": {"ConformEnqueueBody": {
        "additionalProperties": False, "required": ["ref", "dubs"],
        "properties": {"ref": {}, "dubs": {}, "out_dir": {}, "keep_tmp": {}, "label": {}}}}},
}
BODY = {"ref": "/r.mp4", "dubs": ["/d.mp4"], "out_dir": "/o", "cache_dir": "/c", "keep_tmp": False, "label": "L"}


def daemon(openapi, seen):
    """A daemon that serves its schema (or fails) and keeps the job bodies it was sent."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/openapi.json":
            seen["schema_reads"] = seen.get("schema_reads", 0) + 1
            return httpx.Response(200, json=openapi) if openapi is not None else httpx.Response(500)
        if request.url.path == "/health":
            return httpx.Response(200, json={"version": "0.1.0"})
        if request.url.path == "/conform/device":
            return httpx.Response(200, json={"device": "cuda", "gpu": True, "name": "RTX"})
        seen.setdefault("bodies", []).append(json.loads(request.content))
        return httpx.Response(200, json={"id": "cf1"})
    return DaemonClient("http://daemon.test", httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def test_the_schema_names_the_fields_a_job_body_may_carry():
    assert accepted_fields(SCHEMA) == {"ref", "dubs", "out_dir", "keep_tmp", "label"}


@pytest.mark.parametrize("document", [
    None, {}, {"paths": {}}, "not a schema",
    {**SCHEMA, "components": {"schemas": {"ConformEnqueueBody": {"properties": {"ref": {}}}}}},
    {**SCHEMA, "components": {"schemas": {"ConformEnqueueBody": {"additionalProperties": True, "properties": {"ref": {}}}}}},
])
def test_a_schema_that_cannot_be_read_or_allows_any_field_names_none(document):
    assert accepted_fields(document) is None


@pytest.mark.asyncio
async def test_a_job_carries_only_the_fields_the_daemon_accepts():
    seen = {}

    await daemon(SCHEMA, seen).enqueue(dict(BODY))

    assert seen["bodies"] == [{"ref": "/r.mp4", "dubs": ["/d.mp4"], "out_dir": "/o", "keep_tmp": False, "label": "L"}]


@pytest.mark.asyncio
@pytest.mark.parametrize("openapi", [None, {"paths": {}}])
async def test_a_schema_that_cannot_be_read_sends_the_body_as_it_is(openapi):
    seen = {}

    await daemon(openapi, seen).enqueue(dict(BODY))

    assert seen["bodies"] == [BODY]


@pytest.mark.asyncio
async def test_the_schema_is_read_once_and_again_after_the_daemon_is_asked_about_itself():
    seen = {}
    client = daemon(SCHEMA, seen)

    await client.enqueue(dict(BODY))
    await client.enqueue(dict(BODY))
    assert seen["schema_reads"] == 1

    await client.info()
    await client.enqueue(dict(BODY))
    assert seen["schema_reads"] == 2


@pytest.mark.asyncio
async def test_an_unreadable_schema_is_asked_for_again_at_the_next_job():
    seen = {}
    client = daemon(None, seen)

    await client.enqueue(dict(BODY))
    await client.enqueue(dict(BODY))

    assert seen["schema_reads"] == 2
