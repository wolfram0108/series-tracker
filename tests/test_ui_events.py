"""The event names the interface listens to are the names the gateway sends (SSE_MAP): they reach the interface from that
one table, through the schema of the API, and the interface keeps no list of its own."""
import re
from pathlib import Path

from core import Bus
from modules.gateway import GatewayModule
from modules.gateway.module import SSE_MAP

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web" / "src"


def sent() -> list[str]:
    return list(dict.fromkeys(out.name for out in SSE_MAP.values()))


def test_the_schema_names_the_events_of_the_table():
    schema = GatewayModule(Bus(), docs_enabled=True)._create_app().openapi()
    assert schema["components"]["schemas"]["StreamEvent"]["properties"]["event"]["enum"] == sent()


def test_the_interface_types_the_events_by_the_schema_and_keeps_no_list_of_them():
    source = (WEB / "composables" / "useSSE.ts").read_text(encoding="utf-8")
    assert 'components["schemas"]["StreamEvent"]["event"]' in source
    assert not re.search(r'"(%s)"' % "|".join(sent()), source), "useSSE.ts: список событий ведётся вручную"
    generated = (WEB / "api" / "schema.d.ts").read_text(encoding="utf-8")
    typed = re.search(r"StreamEvent: \{\s*/\*\* @enum \{string\} \*/\s*event: ([^;]+);", generated)
    assert typed, "schema.d.ts: StreamEvent не сгенерирован (npm run gen:api)"
    assert re.findall(r'"([a-z_]+)"', typed.group(1)) == sent(), "schema.d.ts отстала от SSE_MAP (npm run gen:api)"


def test_every_event_the_interface_listens_to_is_sent():
    heard = set()
    for path in WEB.rglob("*"):
        if path.suffix in (".ts", ".vue") and ".test." not in path.name:
            heard |= set(re.findall(r'\.on\("([a-z_]+)"', path.read_text(encoding="utf-8")))
    assert heard, "no listener of the stream found"
    assert sorted(heard - set(sent())) == []
