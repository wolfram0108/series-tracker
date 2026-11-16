"""A secret is told as a mask wherever the bus is written down (core/secrets.py, the dump of a run in modules/runlog)."""
from core.secrets import MASK, redacted


def test_the_fields_that_hold_a_secret_are_masked_wherever_they_lie_and_nothing_else_is():
    payload = {"service": "kinozal", "password": "p4ss", "nested": {"secret": "s3cret", "list": [{"password": "x"}, 1, "word"]}, "url": "http://h"}
    assert redacted(payload) == {"service": "kinozal", "password": MASK, "nested": {"secret": MASK, "list": [{"password": MASK}, 1, "word"]}, "url": "http://h"}
    assert payload["password"] == "p4ss"  # a copy: what is sent is not touched


def test_the_value_of_a_secret_setting_is_masked_by_its_key_and_that_of_another_is_not():
    assert redacted({"key": "tmdb_token", "value": "t0ken"}) == {"key": "tmdb_token", "value": MASK}
    assert redacted({"key": "provider.token", "value": "t"}) == {"key": "provider.token", "value": MASK}
    assert redacted({"key": "sources_term_hours", "value": "3"}) == {"key": "sources_term_hours", "value": "3"}


def test_an_empty_secret_is_not_made_to_look_set():
    assert redacted({"password": "", "secret": None}) == {"password": "", "secret": None}


def test_a_field_key_that_holds_no_name_of_a_setting_is_told_as_it_is_and_breaks_nothing():
    from core.secrets import MASK, redacted
    told = redacted({"key": ["dub:1", "dub:2"], "value": "v", "inner": {"key": "tmdb_token", "value": "secret-token"}})
    assert told == {"key": ["dub:1", "dub:2"], "value": "v", "inner": {"key": "tmdb_token", "value": MASK}}
