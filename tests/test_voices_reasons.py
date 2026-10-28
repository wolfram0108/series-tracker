"""Tests of the reasons a voice-over is refused: codes the interface words, not sentences."""
import re
from pathlib import Path

from modules.voices.reasons import EXTERNAL, parse, reason

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


def test_every_code_the_module_can_give_has_a_wording_in_the_catalog():
    used = set()
    for source in (ROOT / "modules" / "voices").glob("*.py"):
        used |= set(re.findall(r'\breason\(\s*"([a-z_]+)"', source.read_text(encoding="utf-8")))
    catalog = (ROOT / "web" / "src" / "i18n" / "ru.ts").read_text(encoding="utf-8")
    assert used and not [code for code in sorted(used) if f'"voices.reason.{code}"' not in catalog]
