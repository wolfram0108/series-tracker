"""The event names the interface listens to are the names the gateway sends (SSE_MAP), no more and no less."""
import re
from pathlib import Path

from modules.gateway.module import SSE_MAP

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web" / "src"


def interface_events() -> set[str]:
    source = (WEB / "composables" / "useSSE.ts").read_text(encoding="utf-8")
    block = re.search(r"SSE_EVENTS\s*=\s*\[(.*?)\]\s*as const", source, re.S)
    assert block, "useSSE.ts: список SSE_EVENTS не найден"
    return set(re.findall(r'"([a-z_]+)"', block.group(1)))


def test_the_interface_listens_to_exactly_the_events_the_gateway_sends():
    sent = {name for name, _transform in SSE_MAP.values()}
    heard = interface_events()
    assert heard == sent, f"нет в интерфейсе: {sorted(sent - heard)}; лишнее в интерфейсе: {sorted(heard - sent)}"

