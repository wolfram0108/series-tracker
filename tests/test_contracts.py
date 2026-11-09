"""The contracts between modules, checked from both sides: the producer's model, the consumer's reading,
the interface's types, and the one place each policy lives in."""
import importlib
import inspect
import re
from pathlib import Path

import pytest

from core import Bus
from core.contracts import (AlignmentJob, Exclusion, JobStatus, Level, PlannerView, ReferenceMark, RowState,
                            SeriesKind, SourceStatus, SourceType, SubmitOutcome, SubmitReply, TableState)
from modules.conform.module import finished_view
from modules.gateway import GatewayModule
from modules.gateway.module import EVENT_PACKETS
from modules.sources.sites import SiteRegistry
from modules.sources.sites.builtin import builtin_sites

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_TS = (ROOT / "web" / "src" / "api" / "schema.d.ts").read_text(encoding="utf-8")
CATALOG = (ROOT / "web" / "src" / "i18n" / "ru.ts").read_text(encoding="utf-8")


def ts_block(name: str) -> str:
    start = SCHEMA_TS.index(f"        {name}: ")
    return SCHEMA_TS[start:SCHEMA_TS.index("\n        };", start)]


@pytest.mark.parametrize("enum", [ReferenceMark, SourceType, SeriesKind, SourceStatus, RowState, TableState, Exclusion,
                                  Level])
def test_every_value_of_a_public_enum_is_in_the_generated_types(enum):
    block = ts_block(enum.__name__)
    assert all(f'"{member.value}"' in block for member in enum), enum.__name__


@pytest.mark.parametrize("model", EVENT_PACKETS)
def test_the_generated_types_have_the_fields_of_the_models(model):
    fields = set(re.findall(r"^\s{12}(\w+)\??:", ts_block(model.__name__), re.MULTILINE))
    assert fields == set(model.model_fields), "run the type generation after changing a contract model"


def test_the_api_schema_describes_the_packets_of_the_event_stream():
    schemas = GatewayModule(Bus(), docs_enabled=True, auth_required=False)._create_app().openapi()["components"]["schemas"]
    assert {model.__name__ for model in EVENT_PACKETS} | {"ReferenceMark"} <= set(schemas)
    assert set(schemas["PlannerView"]["properties"]) == set(PlannerView.model_fields)


def test_a_reply_to_a_submission_is_one_of_four_outcomes_and_anything_else_is_not_a_reply():
    from pydantic import ValidationError
    assert SubmitReply.accepted("j1").outcome is SubmitOutcome.ACCEPTED
    assert SubmitReply.unavailable("down", retry=True).retry is True
    for built in (SubmitReply.rejected("no"), SubmitReply.uncertain("lost")):
        assert SubmitReply.model_validate(built.model_dump()) == built
    for unclear in ({"job_id": "j1"}, {"rejected": "no"}, {"outcome": "accepted"}, {"outcome": "uncertain"},
                    {"outcome": "maybe", "reason": "x"}, {}):
        with pytest.raises(ValidationError):
            SubmitReply.model_validate(unclear)


def test_the_end_of_a_job_is_built_and_read_with_one_model():
    view = finished_view({"id": "j1", "status": "done", "error": None, "results": [
        {"dub": "a__rus__x__720p.mp4", "ok": True, "out_path": "/mnt/meganas/media/x/a.flac",
         "audio_resid_ms": 12.0, "suspect": False, "critical": [], "warnings": []}]},
        {"/mnt/meganas/media": "/nas"})
    job = AlignmentJob.model_validate(view)
    assert job.status is JobStatus.DONE and job.results[0].out_path == "/nas/x/a.flac"


A_PAGE_OF = {"ruchime": "https://1.ruchime.org/release/x/episode-3?off=vk#top",
             "dunhuatv": "https://www.dunhuatv.ru/28-x-smotret-onlajn.html?a=1#cvh=2",
             "animakima": "https://animakima.ru/pages/vechnaya-volya-4-tv-4?a=1#animakima=1/2",
             "vk_channel": "https://vk.com/@chan/videos|запрос",
             "animesss": "https://animesss.com/aniserials/video/action/1171-x.html?a=1#top",
             "pvashow": "https://anime.pvashow.com/4-boevoj-master.html?a=1",
             "yummyani": "https://ru.yummyani.me/catalog/item/lyubov-pod-gipnozom/?a=1",
             "maccms": "https://jszyapi.com/index.php/vod/detail/id/4794.html?ac=detail"}


def test_every_registered_site_declares_what_the_contract_asks():
    sites = builtin_sites()
    registry = SiteRegistry(sites)
    assert sites and len({site.name for site in sites}) == len(sites)
    for site in sites:
        assert isinstance(site.name, str) and site.name
        assert site.domains and all(isinstance(domain, str) for domain in site.domains)
        assert inspect.iscoroutinefunction(site.read)
        page = A_PAGE_OF[site.name]
        # The registry finds the site of its own page, and the canonical address reads back as itself.
        assert registry.find(page) is site and site.match_url(page)
        canonical = site.canonical_url(page)
        assert site.canonical_url(canonical) == canonical and registry.find(canonical) is site
