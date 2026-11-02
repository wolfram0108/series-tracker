"""The registry of output levers: the four of the stage, what each accepts as a value and as a target, and the
registration of one more."""
import pytest

from core.contracts import LeverSpec, TargetKind, ValueShape, group_target, variant_target
from modules.composition import levers
from modules.composition.levers import Lever

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
    assert [(each.name, each.target, each.shape, each.default, each.consumer) for each in levers.all()] == [
        ("reference.choice", TargetKind.NONE, ValueShape.REFERENCE_PICK, None, "scan"),
        ("variant.allow", TargetKind.VARIANT, ValueShape.FLAG, True, "scan"),
        ("voice.take", TargetKind.GROUP, ValueShape.FLAG, True, "voices"),
        ("voice.player", TargetKind.GROUP, ValueShape.VARIANT_PICK, None, "voices")]
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
    ("variant.allow", True, True),
    ("variant.allow", False, True),
    ("variant.allow", None, False),
    ("variant.allow", 1, False),
    ("variant.allow", "yes", False),
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
    ("variant.allow", variant_target("Ruchime", 3, "vibix"), True),
    ("variant.allow", variant_target("A / B", 3, "a/b"), True),
    ("variant.allow", variant_target('Q"uote', 0, "p"), True),
    ("variant.allow", None, False),
    ("variant.allow", group_target("Ruchime"), False),
    ("variant.allow", "variant:", False),
    ("variant.allow", 'variant:["Ruchime",3]', False),
    ("variant.allow", 'variant:["Ruchime","3","vk"]', False),
    ("variant.allow", 'variant:["Ruchime",true,"vk"]', False),
    ("variant.allow", 'variant:["",3,"vk"]', False),
    ("variant.allow", 'variant:["Ruchime",3,""]', False),
    ("variant.allow", 'variant: ["Ruchime", 3, "vk"]', False),
    ("variant.allow", "source:3/player:vk", False),
    ("variant.allow", 3, False),
    ("voice.take", group_target("Ruchime"), True),
    ("voice.take", group_target("AniMy / RuChiMe"), True),
    ("voice.take", None, False),
    ("voice.take", "Ruchime", False),
    ("voice.take", "group:", False),
    ("voice.take", variant_target("Ruchime", 3, "vibix"), False),
    ("voice.player", group_target("Ruchime"), True),
    ("voice.player", None, False),
])
def test_a_lever_points_only_at_the_kind_of_thing_it_is_about(name, target, accepted):
    assert levers.get(name).accepts_target(target) is accepted


@pytest.mark.parametrize("name, target, value, expected", [
    ("reference.choice", None, CHOICE, {3}),
    ("reference.choice", None, None, set()),
    ("variant.allow", variant_target("Ruchime", 3, "vibix"), False, {3}),
    ("voice.take", group_target("Ruchime"), False, set()),
    ("voice.player", group_target("Ruchime"), PICK, {2}),
    ("voice.player", group_target("Ruchime"), None, set()),
])
def test_a_record_names_the_sources_it_points_at_by_its_target_and_by_its_value(name, target, value, expected):
    assert levers.get(name).sources_of(target, value) == expected


def test_a_new_lever_is_one_registration_and_is_found_in_the_registry_and_in_the_specs(registered):
    fake = registered(Lever("fake.mute", TargetKind.VARIANT, ValueShape.FLAG, False, "voices"))
    assert levers.get("fake.mute") is fake and levers.all()[-1] is fake
    assert fake.accepts(True) and not fake.accepts(7)
    assert fake.accepts_target(variant_target("Ruchime", 3, "vk")) and not fake.accepts_target(None)
    assert levers.specs()[-1] == LeverSpec(name="fake.mute", target=TargetKind.VARIANT, shape=ValueShape.FLAG,
                                           default=False, consumer="voices")


def test_the_specs_describe_every_lever_the_registry_has_in_its_order():
    assert [(each.name, each.target, each.shape) for each in levers.specs()] == [
        (each.name, each.target, each.shape) for each in levers.all()]


def test_a_name_is_taken_once(registered):
    registered(Lever("fake.volume", TargetKind.NONE, ValueShape.FLAG, True, "voices"))
    with pytest.raises(ValueError):
        levers.register(Lever("fake.volume", TargetKind.GROUP, ValueShape.FLAG, False, "scan"))
    with pytest.raises(ValueError):
        levers.register(Lever("voice.take", TargetKind.GROUP, ValueShape.FLAG, True, "voices"))
    assert levers.get("fake.volume").default is True and levers.get("voice.take").consumer == "voices"


def test_a_lever_that_is_taken_out_is_gone_and_taking_out_a_missing_one_is_harmless():
    levers.register(Lever("fake.volume", TargetKind.NONE, ValueShape.FLAG, True, "voices"))
    levers.unregister("fake.volume")
    levers.unregister("fake.volume")
    assert levers.get("fake.volume") is None
    assert [each.name for each in levers.all()] == ["reference.choice", "variant.allow", "voice.take", "voice.player"]


def test_a_lever_of_a_form_no_window_draws_is_refused_when_it_is_registered():
    from core.contracts import LEVER_FORMS
    for target in TargetKind:
        for shape in ValueShape:
            lever = Lever(f"fake.{target}.{shape}", target, shape, None, "voices")
            if (target, shape) in LEVER_FORMS:
                levers.register(lever)
                levers.unregister(lever.name)
            else:
                with pytest.raises(ValueError, match="no window draws"):
                    levers.register(lever)
                assert levers.get(lever.name) is None


def test_the_forms_the_core_allows_are_the_forms_the_window_draws():
    """The two lists are written once on each side; this keeps them one list."""
    import re
    from pathlib import Path
    from core.contracts import LEVER_FORMS
    text = (Path(__file__).resolve().parent.parent / "web" / "src" / "lib" / "output.ts").read_text(encoding="utf-8")
    block = re.search(r"RENDERED_FORMS[^=]*=\s*\[(.*?)\]\s*as const", text, re.S)
    assert block, "web/src/lib/output.ts names no RENDERED_FORMS"
    drawn = {(each.group(1), each.group(2)) for each in re.finditer(r'"(\w+)/(\w+)"', block.group(1))}
    assert drawn == {(str(target), str(shape)) for target, shape in LEVER_FORMS}
