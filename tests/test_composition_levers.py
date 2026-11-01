"""The registry of output levers: the four of the stage, what each accepts as a value and as a target, and the
registration of one more."""
import pytest

from core.contracts import group_target, player_target
from modules.composition import levers
from modules.composition.levers import Lever, TargetKind

CHOICE = {"group": "Ruchime", "source_id": 3, "player": "vibix"}
PICK = {"source_id": 2, "player": "kodik"}


@pytest.fixture
def registered():
    """Levers a test adds are taken out again: the registry is shared by every test of the process."""
    added = []

    def add(lever: Lever) -> Lever:
        levers.register(lever)
        added.append(lever.name)
        return lever

    yield add
    for name in added:
        levers.unregister(name)


def test_the_levers_of_the_stage_are_registered_with_their_kind_of_target_default_and_consumer():
    assert [(each.name, each.target, each.default, each.consumer) for each in levers.all()] == [
        ("reference.choice", TargetKind.NONE, None, "scan"),
        ("reference.allow", TargetKind.PLAYER, True, "scan"),
        ("voice.take", TargetKind.GROUP, True, "voices"),
        ("voice.player", TargetKind.GROUP, None, "voices")]
    assert levers.get("voice.take") is levers.all()[2]
    assert levers.get("nothing.here") is None


@pytest.mark.parametrize("name, value, accepted", [
    ("reference.choice", None, True),
    ("reference.choice", CHOICE, True),
    ("reference.choice", {"group": "Ruchime", "source_id": 3}, False),
    ("reference.choice", {**CHOICE, "extra": 1}, False),
    ("reference.choice", {**CHOICE, "source_id": "3"}, False),
    ("reference.choice", {**CHOICE, "source_id": True}, False),
    ("reference.choice", {**CHOICE, "group": ""}, False),
    ("reference.choice", {**CHOICE, "player": 5}, False),
    ("reference.choice", {}, False),
    ("reference.choice", "auto", False),
    ("reference.choice", True, False),
    ("reference.allow", True, True),
    ("reference.allow", False, True),
    ("reference.allow", None, False),
    ("reference.allow", 1, False),
    ("reference.allow", "yes", False),
    ("voice.take", True, True),
    ("voice.take", False, True),
    ("voice.take", 0, False),
    ("voice.take", None, False),
    ("voice.player", None, True),
    ("voice.player", PICK, True),
    ("voice.player", {"player": "kodik"}, False),
    ("voice.player", {**PICK, "group": "Ruchime"}, False),
    ("voice.player", "kodik", False),
    ("voice.player", False, False),
])
def test_a_lever_takes_only_the_values_of_its_kind(name, value, accepted):
    assert levers.get(name).accepts(value) is accepted


@pytest.mark.parametrize("name, target, accepted", [
    ("reference.choice", None, True),
    ("reference.choice", group_target("Ruchime"), False),
    ("reference.allow", player_target(3, "vibix"), True),
    ("reference.allow", player_target(3, "a/b"), True),
    ("reference.allow", None, False),
    ("reference.allow", group_target("Ruchime"), False),
    ("reference.allow", "source:3", False),
    ("reference.allow", "source:3/player:", False),
    ("reference.allow", "source:03/player:vk", False),
    ("reference.allow", "source:x/player:vk", False),
    ("reference.allow", 3, False),
    ("voice.take", group_target("Ruchime"), True),
    ("voice.take", group_target("AniMy / RuChiMe"), True),
    ("voice.take", None, False),
    ("voice.take", "Ruchime", False),
    ("voice.take", "group:", False),
    ("voice.take", player_target(3, "vibix"), False),
    ("voice.player", group_target("Ruchime"), True),
    ("voice.player", None, False),
])
def test_a_lever_points_only_at_the_kind_of_thing_it_is_about(name, target, accepted):
    assert levers.get(name).accepts_target(target) is accepted


@pytest.mark.parametrize("name, target, value, expected", [
    ("reference.choice", None, CHOICE, {3}),
    ("reference.choice", None, None, set()),
    ("reference.allow", player_target(3, "vibix"), False, {3}),
    ("voice.take", group_target("Ruchime"), False, set()),
    ("voice.player", group_target("Ruchime"), PICK, {2}),
    ("voice.player", group_target("Ruchime"), None, set()),
])
def test_a_record_names_the_sources_it_points_at_by_its_target_and_by_its_value(name, target, value, expected):
    assert levers.get(name).sources_of(target, value) == expected


def test_a_new_lever_is_one_registration_and_is_found_in_the_registry(registered):
    fake = registered(Lever("fake.volume", TargetKind.GROUP, lambda value: value in (0, 50, 100), 50, "voices"))
    assert levers.get("fake.volume") is fake and levers.all()[-1] is fake
    assert fake.accepts(100) and not fake.accepts(7)
    assert fake.accepts_target(group_target("Ruchime")) and not fake.accepts_target(None)


def test_a_name_is_taken_once(registered):
    registered(Lever("fake.volume", TargetKind.NONE, lambda value: True, 1, "voices"))
    with pytest.raises(ValueError):
        levers.register(Lever("fake.volume", TargetKind.GROUP, lambda value: True, 2, "scan"))
    with pytest.raises(ValueError):
        levers.register(Lever("voice.take", TargetKind.GROUP, lambda value: True, True, "voices"))
    assert levers.get("fake.volume").default == 1 and levers.get("voice.take").consumer == "voices"


def test_a_lever_that_is_taken_out_is_gone_and_taking_out_a_missing_one_is_harmless():
    levers.register(Lever("fake.volume", TargetKind.NONE, lambda value: True, 1, "voices"))
    levers.unregister("fake.volume")
    levers.unregister("fake.volume")
    assert levers.get("fake.volume") is None
    assert [each.name for each in levers.all()] == ["reference.choice", "reference.allow", "voice.take", "voice.player"]
