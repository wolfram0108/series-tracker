"""Guard on the API schema: one operation id per operation, whatever the methods of a route."""
import warnings
from collections import Counter

import pytest

from core import Bus
from modules.gateway import GatewayModule

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
