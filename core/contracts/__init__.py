"""Messages that cross a module border, described once.

A producer builds its payload with one of these models and a consumer reads it with the same one,
so the two cannot drift apart unnoticed. Modules import their contracts from here, never from each other.
"""
from .alignment import AlignmentJob, DubResult, JobStatus, SubmitOutcome, SubmitReply
from .composition import (CompositionChanged, CompositionFailed, CompositionGetRequest, CompositionReply,
                          CompositionRow, EpisodeRef, Exclusion, PlayerSummary, SourceStatus, SourceSummary,
                          VoiceSummary)
from .conform_settings import ConformSettingsDraft, ConformSettingsSaved, ConformSettingsView
from .output_settings import (REFERENCE_ALLOW, REFERENCE_CHOICE, VOICE_PLAYER, VOICE_TAKE, EffectiveLever, EpisodeSettings, Level, LevelSettings, PlayerPick, ReferenceChoice, SettingRecord, SettingsReset, SettingsSet,
                              group_target, parse_group_target, parse_player_target, player_target)
from .reference import Reference, ReferenceChanged, ReferenceMark
from .release import Release, ReleaseSource, ReleaseTitle
from .release_table import (LINE_SEPARATOR, ReleaseTable, RowState, RowVoice, SiteResolveReply, SiteResolveRequest,
                            TableFailed, TableGetReply, TableGetRequest, TableProgress, TableReady, TableRow, TableState, line_of)
from .rules import RuleLine, RuleResult, RulesApplyReply, RulesApplyRequest, RulesProfileChanged
from .series_sources import SeriesKind, SeriesSource, SourceEvent, SourceType
from .voice_groups import GroupsResolveReply, GroupsResolveRequest
from .voices import (DubEvent, DubEventStatus, DubHint, DubItem, DubRecord, DubsSnapshot, DubState, DubStatus,
                     Layout, LayoutRole, LayoutVoice, Reason, SettingProblem, SkippedSource, SnapshotItem,
                     VoiceSettingsDraft, VoiceSettingsSaved, VoiceSettingsView)

__all__ = ["AlignmentJob", "DubResult", "JobStatus", "SubmitOutcome", "SubmitReply", "Release", "ReleaseSource", "ReleaseTitle", "DubEvent",
           "DubEventStatus", "DubHint", "DubItem", "DubRecord", "DubsSnapshot", "DubState", "DubStatus", "Layout", "LayoutRole",
           "LayoutVoice", "Reason", "SnapshotItem", "SettingProblem", "SkippedSource", "VoiceSettingsDraft",
           "VoiceSettingsSaved", "VoiceSettingsView", "ConformSettingsDraft", "ConformSettingsSaved",
           "ConformSettingsView",
           "LINE_SEPARATOR", "ReleaseTable", "RowState", "RowVoice", "SiteResolveReply", "SiteResolveRequest", "TableFailed", "TableGetReply", "TableGetRequest",
           "TableProgress", "TableReady", "TableRow", "TableState", "line_of",
           "RuleLine", "RuleResult", "RulesApplyReply", "RulesApplyRequest", "RulesProfileChanged",
           "SeriesKind", "SeriesSource", "SourceEvent", "SourceType",
           "CompositionChanged", "CompositionFailed", "CompositionGetRequest", "CompositionReply", "CompositionRow",
           "EpisodeRef", "Exclusion", "PlayerSummary", "SourceStatus", "SourceSummary", "VoiceSummary",
           "REFERENCE_ALLOW", "REFERENCE_CHOICE", "VOICE_PLAYER", "VOICE_TAKE", "EffectiveLever", "EpisodeSettings", "Level", "LevelSettings", "PlayerPick", "ReferenceChoice", "SettingRecord", "SettingsReset", "SettingsSet",
           "group_target", "parse_group_target", "parse_player_target", "player_target",
           "Reference", "ReferenceChanged", "ReferenceMark",
           "GroupsResolveReply", "GroupsResolveRequest"]
