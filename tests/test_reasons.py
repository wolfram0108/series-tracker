"""Tests of reasons: codes the interface words, not sentences."""
import re
from pathlib import Path

from core.reasons import EXTERNAL, describe, parse, reason

ROOT = Path(__file__).resolve().parent.parent


def test_a_coded_reason_reaches_the_interface_as_code_and_facts():
    assert parse(reason("job_ended", status="cancelled")) == {
        "code": "job_ended", "params": {"status": "cancelled"}}
    assert parse(reason("file_vanished")) == {"code": "file_vanished", "params": {}}


def test_words_of_another_system_are_kept_as_they_came():
    assert parse("HTTP 404 от плеера") == {"code": EXTERNAL, "params": {"text": "HTTP 404 от плеера"}}
    assert parse('{"not": "a reason"}')["code"] == EXTERNAL


def test_no_reason_is_none():
    assert parse(None) is None and parse("") is None


def test_a_reason_in_a_log_line_is_the_code_and_its_facts():
    assert describe(reason("player_status", player="VK", status=503)) == "player_status(player=VK, status=503)"
    assert describe(reason("episode_failed", cause=reason("player_gone"))) == "episode_failed(cause=player_gone)"
    assert describe("HTTP 404") == "HTTP 404" and describe(None) == ""


def test_every_code_any_module_can_give_has_a_wording_in_the_catalog():
    used = set()
    for source in (ROOT / "modules").rglob("*.py"):
        used |= set(re.findall(r'\breason\(\s*"([a-z_]+)"', source.read_text(encoding="utf-8")))
    catalog = (ROOT / "web" / "src" / "i18n" / "ru.ts").read_text(encoding="utf-8")
    assert used and not [code for code in sorted(used) if f'"reason.{code}"' not in catalog]
