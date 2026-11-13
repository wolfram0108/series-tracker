"""The registry of output levers: the levers of the queues, what each accepts as a value and as a target, and the
registration of one more."""
import pytest

from core.contracts import LeverSpec, Queue, TargetKind, ValueShape, group_target, variant_target
from modules.composition import levers
from modules.composition.levers import Lever

REF = {"group": "Ruchime", "source_id": 3, "player": "vibix"}
OTHER_REF = {"group": "AniStar", "source_id": 2, "player": "kodik"}
VOICE = {"source_id": 2, "player": "kodik"}
OTHER_VOICE = {"source_id": 3, "player": "vk"}


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


def test_the_levers_of_the_queues_are_registered_with_their_target_shape_default_consumer_and_queue():
    assert [(each.name, each.target, each.shape, each.default, each.consumer, each.queue) for each in levers.all()] == [
        ("reference.order", TargetKind.NONE, ValueShape.VARIANT_ORDER, [], "scan", Queue.REFERENCE),
        ("reference.allow", TargetKind.VARIANT, ValueShape.FLAG, True, "scan", Queue.REFERENCE),
        ("voice.take", TargetKind.GROUP, ValueShape.FLAG, True, "voices", Queue.VOICE),
        ("voice.order", TargetKind.GROUP, ValueShape.VARIANT_ORDER, [], "voices", Queue.VOICE),
        ("voice.allow", TargetKind.VARIANT, ValueShape.FLAG, True, "voices", Queue.VOICE),
        ("compilation.member", TargetKind.NONE, ValueShape.COMPILATION, None, "composition", Queue.COMPILATION),
        ("compilation.take", TargetKind.NONE, ValueShape.FLAG, True, "composition", Queue.COMPILATION),
        ("markup.order", TargetKind.NONE, ValueShape.VARIANT_ORDER, [], "planner", Queue.MARKUP),
        ("markup.allow", TargetKind.VARIANT, ValueShape.FLAG, True, "planner", Queue.MARKUP),
        ("reference.same", TargetKind.NONE, ValueShape.VARIANT_SETS, [], "planner", Queue.REFERENCE),
        ("markup.same", TargetKind.NONE, ValueShape.VARIANT_SETS, [], "planner", Queue.MARKUP)]
    assert levers.get("voice.take") is levers.all()[2]
    assert levers.get("nothing.here") is None


@pytest.mark.parametrize("name, value, accepted", [
    ("reference.order", [], True),
    ("reference.order", [REF], True),
    ("reference.order", [REF, OTHER_REF], True),
    ("reference.order", [REF, REF], False),
    ("reference.order", None, False),
    ("reference.order", REF, False),
    ("reference.order", [{"group": "Ruchime", "source_id": 3}], False),
    ("reference.order", [{**REF, "extra": 1}], False),
    ("reference.order", [{**REF, "source_id": "3"}], False),
    ("reference.order", [{**REF, "source_id": True}], False),
    ("reference.order", [{**REF, "group": ""}], False),
    ("reference.order", [{**REF, "player": 5}], False),
    ("reference.order", [VOICE], False),
    ("reference.order", "auto", False),
    ("reference.allow", True, True),
    ("reference.allow", False, True),
    ("reference.allow", None, False),
    ("reference.allow", 1, False),
    ("voice.allow", False, True),
    ("voice.allow", "yes", False),
    ("voice.take", True, True),
    ("voice.take", False, True),
    ("voice.take", 0, False),
    ("voice.take", None, False),
    ("voice.order", [], True),
    ("voice.order", [VOICE, OTHER_VOICE], True),
    ("voice.order", [VOICE, VOICE], False),
    ("voice.order", [{"player": "kodik"}], False),
    ("voice.order", [{**VOICE, "group": "Ruchime"}], False),
    ("voice.order", VOICE, False),
    ("voice.order", None, False),
    # The variants the user declared equal: groups of two or more, a variant in one group only.
    ("reference.same", [], True),
    ("reference.same", [[REF, OTHER_REF]], True),
    ("reference.same", [[REF, OTHER_REF], [{**REF, "player": "kodik"}, {**OTHER_REF, "player": "vk"}]], True),
    ("markup.same", [[REF, OTHER_REF]], True),
    ("reference.same", [[REF]], False),
    ("reference.same", [[]], False),
    ("reference.same", [[REF, REF]], False),
    ("reference.same", [[REF, OTHER_REF], [OTHER_REF, {**REF, "player": "vk"}]], False),
    ("reference.same", [REF, OTHER_REF], False),
    ("reference.same", [[REF, VOICE]], False),
    ("reference.same", [[REF, {**OTHER_REF, "extra": 1}]], False),
    ("reference.same", None, False),
    ("reference.same", "auto", False),
])
def test_a_lever_takes_only_the_values_of_its_kind(name, value, accepted):
    assert levers.get(name).accepts(value) is accepted


@pytest.mark.parametrize("name, target, accepted", [
    ("reference.order", None, True),
    ("reference.order", group_target("Ruchime"), False),
    ("reference.allow", variant_target("Ruchime", 3, "vibix"), True),
    ("reference.allow", variant_target("A / B", 3, "a/b"), True),
    ("reference.allow", variant_target('Q"uote', 0, "p"), True),
    ("reference.allow", None, False),
    ("reference.allow", group_target("Ruchime"), False),
    ("reference.allow", "variant:", False),
    ("reference.allow", 'variant:["Ruchime",3]', False),
    ("reference.allow", 'variant:["Ruchime","3","vk"]', False),
    ("reference.allow", 'variant:["Ruchime",true,"vk"]', False),
    ("reference.allow", 'variant:["",3,"vk"]', False),
    ("reference.allow", 'variant:["Ruchime",3,""]', False),
    ("reference.allow", 'variant: ["Ruchime", 3, "vk"]', False),
    ("reference.allow", "source:3/player:vk", False),
    ("reference.allow", 3, False),
    ("voice.allow", variant_target("Ruchime", 3, "vibix"), True),
    ("voice.allow", group_target("Ruchime"), False),
    ("voice.take", group_target("Ruchime"), True),
    ("voice.take", group_target("AniMy / RuChiMe"), True),
    ("voice.take", None, False),
    ("voice.take", "Ruchime", False),
    ("voice.take", "group:", False),
    ("voice.take", variant_target("Ruchime", 3, "vibix"), False),
    ("voice.order", group_target("Ruchime"), True),
    ("voice.order", None, False),
])
def test_a_lever_points_only_at_the_kind_of_thing_it_is_about(name, target, accepted):
    assert levers.get(name).accepts_target(target) is accepted


@pytest.mark.parametrize("name, target, value, expected", [
    ("reference.order", None, [REF, OTHER_REF], {3, 2}),
    ("reference.order", None, [], set()),
    ("reference.allow", variant_target("Ruchime", 3, "vibix"), False, {3}),
    ("voice.allow", variant_target("Ruchime", 4, "vibix"), False, {4}),
    ("voice.take", group_target("Ruchime"), False, set()),
    ("voice.order", group_target("Ruchime"), [VOICE], {2}),
    ("voice.order", group_target("Ruchime"), [], set()),
])
def test_a_record_names_the_sources_it_points_at_by_its_target_and_by_its_value(name, target, value, expected):
    assert levers.get(name).sources_of(target, value) == expected


def test_a_new_lever_is_one_registration_and_is_found_in_the_registry_and_in_the_specs(registered):
    fake = registered(Lever("fake.mute", TargetKind.VARIANT, ValueShape.FLAG, False, "voices", Queue.VOICE))
    assert levers.get("fake.mute") is fake and levers.all()[-1] is fake
    assert fake.accepts(True) and not fake.accepts(7)
    assert fake.accepts_target(variant_target("Ruchime", 3, "vk")) and not fake.accepts_target(None)
    assert levers.specs()[-1] == LeverSpec(name="fake.mute", target=TargetKind.VARIANT, shape=ValueShape.FLAG,
                                           default=False, consumer="voices", queue=Queue.VOICE)


def test_the_specs_describe_every_lever_the_registry_has_in_its_order():
    assert [(each.name, each.target, each.shape, each.queue) for each in levers.specs()] == [
        (each.name, each.target, each.shape, each.queue) for each in levers.all()]


def test_a_name_is_taken_once(registered):
    registered(Lever("fake.volume", TargetKind.GROUP, ValueShape.FLAG, True, "voices", Queue.VOICE))
    with pytest.raises(ValueError):
        levers.register(Lever("fake.volume", TargetKind.GROUP, ValueShape.FLAG, False, "scan", Queue.REFERENCE))
    with pytest.raises(ValueError):
        levers.register(Lever("voice.take", TargetKind.GROUP, ValueShape.FLAG, True, "voices", Queue.VOICE))
    assert levers.get("fake.volume").default is True and levers.get("voice.take").consumer == "voices"


def test_a_lever_that_is_taken_out_is_gone_and_taking_out_a_missing_one_is_harmless():
    levers.register(Lever("fake.volume", TargetKind.GROUP, ValueShape.FLAG, True, "voices", Queue.VOICE))
    levers.unregister("fake.volume")
    levers.unregister("fake.volume")
    assert levers.get("fake.volume") is None
    assert [each.name for each in levers.all()] == [
        "reference.order", "reference.allow", "voice.take", "voice.order", "voice.allow", "compilation.member",
        "compilation.take", "markup.order", "markup.allow", "reference.same", "markup.same"]


def test_a_lever_of_a_form_no_window_draws_is_refused_when_it_is_registered():
    from core.contracts import LEVER_FORMS
    for target in TargetKind:
        for shape in ValueShape:
            lever = Lever(f"fake.{target}.{shape}", target, shape, None, "voices", Queue.VOICE)
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
