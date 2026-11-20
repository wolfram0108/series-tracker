"""The assembly of the process: every module of the service, in the order they start. The service (run.py) and the stand
of the whole application (tests/app_stand.py) are built by this one function, so that what a stand runs is the service."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from core.bus import Bus
from core.db import Database
from core.logging import log_file
from modules.auth import AuthModule
from modules.catalog import CatalogModule
from modules.composition import CompositionModule
from modules.conform import ConformModule
from modules.downloads import DownloadsModule
from modules.gateway import GatewayModule
from modules.library import LibraryModule
from modules.metadata import MetadataModule
from modules.planner import PlannerModule
from modules.renaming import RenamingModule
from modules.rules import RulesModule
from modules.runlog import RunlogModule
from modules.scan import ScanModule
from modules.settings import SettingsModule
from modules.sources import SourcesModule
from modules.sources.provider import ProviderClient
from modules.sources.table import utc_now
from modules.torrents import TorrentsModule
from modules.unpack import UnpackModule
from modules.trackerauth import TrackerauthModule
from modules.voices import VoicesModule


@dataclass
class Options:
    """What the process is told by its environment."""

    db_path: str
    secret_key: str = ""
    cookie_secure: bool = True
    auth_required: bool = True
    docs_enabled: bool = False
    allowed_hosts: list[str] | None = None
    read_recent: int | None = None
    conform_url: str = ""
    qbit_url: str = ""
    qbit_username: str = "admin"
    qbit_password: str = ""
    torrent_cache_dir: str = "torrent_cache"
    # The clock the age of a table of a page is told by (a stand moves it; the service runs on the time of day).
    table_clock: Callable[[], datetime] = utc_now
    # The client profile Alloha is asked as (modules/sources/players/alloha_session.py), from the private environment.
    alloha_profile: object = None
    # The provider of the socket of external sources (contracts/provider_socket.md): its address and token; no address, no socket.
    provider_url: str = ""
    provider_token: str = ""
    # The pace the feed of the provider is opened again at after it dropped (the first pause, the ceiling); the default when None.
    provider_reconnect: tuple[float, float] | None = None


def assemble(bus: Bus, db: Database, options: Options) -> tuple[GatewayModule, list]:
    gateway = GatewayModule(bus, db_path=options.db_path, secret_key=options.secret_key,
                            cookie_secure=options.cookie_secure, auth_required=options.auth_required,
                            docs_enabled=options.docs_enabled, allowed_hosts=options.allowed_hosts)
    modules = [
        # The dump of every sync (modules/runlog): first, so that it sees the bus from the first envelope of a start.
        RunlogModule(bus, log_file("runs")),
        gateway,
        CatalogModule(bus, db),
        SettingsModule(bus, db),
        AuthModule(bus, db),
        RulesModule(bus, db),
        TrackerauthModule(bus, db),
        SourcesModule(bus, db, read_recent=options.read_recent, torrent_cache_dir=options.torrent_cache_dir,
                      table_clock=options.table_clock,
                      alloha_profile=options.alloha_profile,
                      provider=ProviderClient(options.provider_url, options.provider_token) if options.provider_url else None,
                      provider_reconnect=options.provider_reconnect),
        MetadataModule(bus, db),
        LibraryModule(bus, db),
        # scan/downloads/renaming after catalog/settings/sources/rules: their reconcile at the start asks those neighbours.
        ScanModule(bus, db),
        DownloadsModule(bus, db),
        RenamingModule(bus),
        VoicesModule(bus, db),
        # The plan of every video series and its sync (stage 12): downloads, cuts, alignments, containers, the journal.
        PlannerModule(bus, db),
        # composition asks catalog, sources, rules and voices at its start and then publishes composition.changed, so
        # scan and the planner must be subscribed by then.
        CompositionModule(bus, db),
        # Alignment daemon (track-muxer): its address is the setting conform.url, the environment's while none is stored.
        ConformModule(bus, default_url=options.conform_url),
        # The environment's qBittorrent first (a stand, tests); without it torrents takes the credentials of the table
        # auth over the bus (Р-22) and reconnects on trackerauth.credentials.changed.
        TorrentsModule(bus, db, qbt_url=options.qbit_url, qbt_username=options.qbit_username,
                       qbt_password=options.qbit_password),
        # The reading of the releases of the torrent sources (stage 15): it makes the preset of the editor at its start
        # (rules, settings) and answers the table of a tracker's page.
        UnpackModule(bus, db),
    ]
    return gateway, modules
