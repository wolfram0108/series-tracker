"""Single filesystem-name rule (core.fsnames). The tables must match
web/src/lib/fsnames.test.ts: both implementations have to agree."""
import pytest

from core.fsnames import sanitize_name, sanitize_path

NAME_CASES = [
    ('Bad: "Name"?', "Bad Name"),
    ("Star Trek: Strange New Worlds", "Star Trek Strange New Worlds"),
    ("What If...?", "What If..."),
    ("a/b\\c|d<e>f*g", "abcdefg"),
    ("  За гранью времени  ", "За гранью времени"),
    ("a \t: b", "a b"),
    ("a\x00b\x1fc", "ab c"),  # \x1f is whitespace, \x00 is not
    ("", ""),
    ("???", ""),
]

PATH_CASES = [
    ("/nas/Series/test/Звёздный путь: Странные новые миры (2022) [tmdbid-1]",
     "/nas/Series/test/Звёздный путь Странные новые миры (2022) [tmdbid-1]"),
    ("C:\\media\\show", "C/media/show"),
    ("/nas/Series fin /x", "/nas/Series fin/x"),
    ("/nas/a?b/c*d|e", "/nas/ab/cde"),
    ("/nas/Series/test/", "/nas/Series/test/"),
    ("  /nas/x  ", "/nas/x"),
    ("", ""),
]


@pytest.mark.parametrize("raw, expected", NAME_CASES)
def test_sanitize_name(raw, expected):
    assert sanitize_name(raw) == expected
    assert sanitize_name(expected) == expected  # idempotent


@pytest.mark.parametrize("raw, expected", PATH_CASES)
def test_sanitize_path(raw, expected):
    assert sanitize_path(raw) == expected
    assert sanitize_path(expected) == expected  # idempotent
