"""Guard on the API schema: one operation id per operation, whatever the methods of a route."""
import warnings
from collections import Counter

import pytest

from core import Bus, BusRequestError
from modules.gateway import GatewayModule
from modules.gateway.routing import refusal

# Routes the legacy front calls with several methods; each method must stay served.
MULTI_METHOD = {
    "/api/settings/force_replace": {"get", "post"},
    "/api/settings/less_strict_scan": {"get", "post"},
    "/api/settings/slicing_delete_source": {"get", "post"},
    "/api/settings/parallel_downloads": {"get", "post"},
    "/api/settings/concurrent_fragments": {"get", "post"},
    "/api/settings/saved_paths": {"get", "post", "delete"},
    "/api/settings/debug_flags": {"get", "post"},
}


@pytest.fixture
def schema(tmp_path):
    gateway = GatewayModule(Bus(), static_dir=str(tmp_path), auth_required=False, docs_enabled=True)
    with warnings.catch_warnings():
        # FastAPI only warns on a repeated id, and the generated types silently take one of them.
        warnings.filterwarnings("error", message="Duplicate Operation ID")
        return gateway.app.openapi()


def test_every_operation_of_the_api_schema_has_its_own_id(schema):
    ids = [op["operationId"] for methods in schema["paths"].values() for op in methods.values()]
    assert [name for name, count in Counter(ids).items() if count > 1] == []


@pytest.mark.parametrize("path", sorted(MULTI_METHOD))
def test_a_route_of_several_methods_serves_each_of_them(schema, path):
    assert set(schema["paths"][path]) == MULTI_METHOD[path]


@pytest.mark.parametrize("message,status", [
    ("LookupError: сериал 1 не найден", 404),
    ("ValueError: {\"code\":\"last_source\"}", 400),
    ("нет обработчика для query 'composition.get'", 503),
    ("RuntimeError: boom", 500)])
def test_a_refusal_of_a_module_is_an_http_status_by_its_kind(message, status):
    assert refusal(BusRequestError(message)).status_code == status


def test_a_refusal_carries_the_text_of_the_module_after_the_name_of_the_error():
    import json
    answer = refusal(BusRequestError('ValueError: {"code":"last_source","source_id":4}'))
    assert json.loads(answer.body) == {"success": False, "error": '{"code":"last_source","source_id":4}'}
    assert json.loads(refusal(BusRequestError("ValueError: x"), 409, with_success=False).body) == {"error": "x"}
    assert refusal(BusRequestError("ValueError: x"), 409).status_code == 409
