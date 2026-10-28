"""The contracts between modules, checked from both sides: the producer's model, the consumer's reading,
the interface's types, and the one place each policy lives in."""
import inspect
import re
from pathlib import Path

import pytest

from core import Bus
from core.contracts import (AlignmentJob, DubEvent, DubEventStatus, DubHint, DubState, DubStatus, DubsSnapshot,
                            JobStatus, LayoutRole)
from modules.conform.module import finished_view
from modules.gateway import GatewayModule
from modules.sources.video_providers import get_default_registry
from modules.voices import alignment, lifecycle, repository
from modules.voices.states import public_hint, public_state

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_TS = (ROOT / "web" / "src" / "api" / "schema.d.ts").read_text(encoding="utf-8")
CATALOG = (ROOT / "web" / "src" / "i18n" / "ru.ts").read_text(encoding="utf-8")


def ts_block(name: str) -> str:
    start = SCHEMA_TS.index(f"        {name}: ")
    return SCHEMA_TS[start:SCHEMA_TS.index("\n        };", start)]


# --- the interface sees the same names as the core ---------------------------------------------


@pytest.mark.parametrize("enum", [DubStatus, DubEventStatus, DubState, DubHint, LayoutRole])
def test_every_value_of_a_public_enum_is_in_the_generated_types(enum):
    block = ts_block(enum.__name__)
    assert all(f'"{member.value}"' in block for member in enum), enum.__name__


@pytest.mark.parametrize("model", [DubEvent, DubsSnapshot])
def test_the_generated_types_have_the_fields_of_the_models(model):
    fields = set(re.findall(r"^\s{12}(\w+)\??:", ts_block(model.__name__), re.MULTILINE))
    assert fields == set(model.model_fields), "run the type generation after changing a contract model"


def test_the_api_schema_describes_the_packets_of_the_event_stream():
    schemas = GatewayModule(Bus(), docs_enabled=True, auth_required=False)._create_app().openapi()["components"]["schemas"]
    assert {"DubEvent", "DubsSnapshot", "DubStatus", "DubState", "ReleaseSource", "Layout"} <= set(schemas)
    assert set(schemas["DubEvent"]["properties"]) == set(DubEvent.model_fields)


def test_every_state_and_hint_has_its_wording_and_every_wording_has_a_state_or_hint():
    for prefix, enum in (("voices.state", DubState), ("voices.hint", DubHint)):
        assert all(f'"{prefix}.{member.value}"' in CATALOG for member in enum), prefix
        wordings = set(re.findall(rf'"{re.escape(prefix)}\.(\w+)"', CATALOG))
        assert wordings == {member.value for member in enum}, prefix


# --- the state shown to the user is total -------------------------------------------------------


@pytest.mark.parametrize("status", list(DubEventStatus))
@pytest.mark.parametrize("job", [None, "queued", "running"])
@pytest.mark.parametrize("fetching", [True, False])
def test_every_step_has_a_state_and_every_state_but_an_error_has_a_hint(status, job, fetching):
    state = public_state(status, job, fetching)
    hint = public_hint(status, state)
    assert isinstance(state, DubState) and (hint is None) == (state is DubState.ERROR)


# --- the life of a row is one table -------------------------------------------------------------


def test_every_step_can_be_reached_from_the_start_by_the_transitions():
    reached, frontier = {DubStatus.PENDING}, [DubStatus.PENDING]
    while frontier:
        step = frontier.pop()
        for transition in lifecycle.TRANSITIONS:
            if step in transition.sources and transition.target not in reached:
                reached.add(transition.target)
                frontier.append(transition.target)
    assert reached == set(DubStatus)


def test_groups_of_steps_are_made_of_real_steps_and_work_does_not_overlap_its_end():
    for group in (lifecycle.WORKING, lifecycle.FINAL, lifecycle.HOLDS_FILES, lifecycle.WAITS_FOR_REFERENCE):
        assert group and group <= set(DubStatus)
    assert not lifecycle.WORKING & lifecycle.FINAL
    assert lifecycle.WORKING | lifecycle.FINAL | {DubStatus.READY, DubStatus.ALIGNED} == set(DubStatus)


def test_the_repository_changes_a_step_only_by_a_declared_transition_and_names_no_step_in_sql():
    source = inspect.getsource(repository)
    named = set(re.findall(r"lifecycle\.([A-Z_]+)\b", source))
    declared = {name for name in dir(lifecycle) if isinstance(getattr(lifecycle, name), lifecycle.Transition)}
    groups = {name for name in dir(lifecycle) if name.isupper() and isinstance(getattr(lifecycle, name), frozenset)}
    assert named <= declared | groups, "the repository names a transition that the lifecycle does not declare"
    assert declared <= named, "a transition is declared that no write uses"
    assert not re.search(r"status\s*=\s*'", source) and not re.search(r"status\s+IN\s*\(\s*'", source)


# --- the alignment service is read in one place -------------------------------------------------


def test_the_end_of_a_job_is_built_and_read_with_one_model():
    view = finished_view({"id": "j1", "status": "done", "error": None, "results": [
        {"dub": "a__rus__x__720p.mp4", "ok": True, "out_path": "/mnt/meganas/media/x/a.flac",
         "audio_resid_ms": 12.0, "suspect": False, "critical": [], "warnings": []}]},
        {"/mnt/meganas/media": "/nas"})
    job = AlignmentJob.model_validate(view)
    assert job.status is JobStatus.DONE and job.results[0].out_path == "/nas/x/a.flac"
    assert alignment.result_for(job, "/tmp/anywhere/a__rus__x__720p.mp4") is job.results[0]
    assert alignment.result_for(job, "/tmp/anywhere/other.mp4") is None


def test_the_aligned_track_is_where_the_module_asked_for_it():
    job = AlignmentJob.model_validate({"job_id": "j", "status": "done", "results": [
        {"dub": "a.mp4", "ok": True, "out_path": "/mnt/daemon/anything/a.flac"}]})
    result = alignment.result_for(job, "/data/7/s01e01/a.mp4")
    assert alignment.track_of("/data/7/s01e01/a.mp4", result) == "/data/7/s01e01/_aligned/a.flac"
    assert alignment.dub_folder_of_track("/data/7/s01e01/_aligned/a.flac") == "/data/7/s01e01"
    assert alignment.track_of("/data/7/s01e01/a.mp4", None) is None


@pytest.mark.parametrize("result,code", [
    (None, "no_result"),
    ({"ok": False}, "align_failed"),
    ({"ok": False, "error": "no match"}, None),
    ({"ok": True, "critical": ["no common sound"]}, "critical"),
    ({"ok": True, "suspect": True, "warnings": ["drift"]}, "suspect"),
    ({"ok": True, "suspect": True}, "suspect_residual"),
    ({"ok": True}, "clean"),
])
def test_a_result_is_admitted_by_the_services_own_verdict_alone(result, code):
    from core.contracts import DubResult
    from modules.voices.reasons import parse
    refusal = alignment.refusal(None if result is None else DubResult(**result))
    if code == "clean":
        assert refusal is None
    elif code is None:
        assert parse(refusal)["params"]["text"] == "no match"
    else:
        assert parse(refusal)["code"] == code


# --- a provider is checked against the same contract --------------------------------------------


def test_every_registered_provider_declares_what_the_contract_asks():
    providers = get_default_registry()._providers
    assert providers
    for provider in providers:
        assert isinstance(provider.name, str) and provider.name
        assert provider.domains and all(isinstance(domain, str) for domain in provider.domains)
        assert isinstance(provider.has_voice_catalog, bool)
