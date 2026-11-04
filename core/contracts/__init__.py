"""Messages that cross a module border, described once.

A producer builds its payload with one of these models and a consumer reads it with the same one,
so the two cannot drift apart unnoticed. Modules import their contracts from here, never from each other.
"""
from .alignment import AlignmentJob, DubResult, JobStatus, SubmitOutcome, SubmitReply
from .composition import (CompositionChanged, CompositionFailed, CompositionGetRequest, CompositionPreviewReply,
                          CompositionPreviewRequest, CompositionReply, PreviewLine,
                          CompositionRow, EpisodeRef, Exclusion, PlayerSummary, SourceStatus, SourceSummary)
from .conform_settings import ConformSettingsDraft, ConformSettingsSaved, ConformSettingsView
from .output_settings import (LEVER_FORMS, REFERENCE_CHOICE, VARIANT_ALLOW, VOICE_PLAYER, VOICE_TAKE, EffectiveLever, EpisodeSettings, LeverSpec,
                              Level, LevelSettings, PlayerPick, ReferenceChoice, SettingRecord, SettingsReset, SettingsSet,
                              TargetKind, ValueShape, group_target, parse_group_target, parse_variant_target, variant_target)
from .reference import OutputSwitch, Reference, ReferenceChanged, ReferenceMark
from .release_table import (LINE_FIELDS, LINE_SEPARATOR, ReleaseTable, RowState, RowVoice, SiteResolveReply, SiteResolveRequest,
                            SeriesReadProgress, TableFailed, TableGetReply, TableGetRequest, TableProgress, TableReady, TableRow,
                            TableState, line_of)
from .rules import RuleLine, RuleResult, RulesApplyReply, RulesApplyRequest, RulesProfileChanged
from .series_sources import SeriesKind, SeriesSource, SourceChanged, SourceEvent, SourceType
from .voice_groups import GroupsResolveReply, GroupsResolveRequest
from .availability import Action, NotAllowed, allowed, refusal, require, variant_state
from .work import RUN_CHANGED, WORK_STATE, WORK_WITHDRAWN, RunChanged, WorkState, WorkWithdrawn
from .voices import (DubEvent, DubEventStatus, DubHint, DubItem, DubRecord, DubsSnapshot, DubState, DubStatus,
                     Reason, SettingProblem, SnapshotItem,
                     VoiceSettingsDraft, VoiceSettingsSaved, VoiceSettingsView)

__all__ = ["Action", "NotAllowed", "allowed", "refusal", "require", "variant_state", "LEVER_FORMS", "WORK_WITHDRAWN", "WorkWithdrawn", "RUN_CHANGED", "WORK_STATE", "RunChanged", "WorkState", "AlignmentJob", "DubResult", "JobStatus", "SubmitOutcome", "SubmitReply", "DubEvent",
           "DubEventStatus", "DubHint", "DubItem", "DubRecord", "DubsSnapshot", "DubState", "DubStatus", "Reason", "SnapshotItem", "SettingProblem", "VoiceSettingsDraft",
           "VoiceSettingsSaved", "VoiceSettingsView", "ConformSettingsDraft", "ConformSettingsSaved",
           "ConformSettingsView",
           "LINE_SEPARATOR", "ReleaseTable", "RowState", "RowVoice", "SiteResolveReply", "SiteResolveRequest", "TableFailed", "TableGetReply", "TableGetRequest",
           "SeriesReadProgress", "TableProgress", "TableReady", "TableRow", "TableState", "line_of", "LINE_FIELDS",
           "RuleLine", "RuleResult", "RulesApplyReply", "RulesApplyRequest", "RulesProfileChanged",
           "SeriesKind", "SeriesSource", "SourceChanged", "SourceEvent", "SourceType",
           "CompositionChanged", "CompositionFailed", "CompositionGetRequest", "CompositionPreviewReply", "CompositionPreviewRequest",
           "CompositionReply", "CompositionRow", "PreviewLine",
           "EpisodeRef", "Exclusion", "PlayerSummary", "SourceStatus", "SourceSummary",
           "REFERENCE_CHOICE", "VARIANT_ALLOW", "VOICE_PLAYER", "VOICE_TAKE", "EffectiveLever", "EpisodeSettings", "LeverSpec", "Level", "LevelSettings", "PlayerPick", "ReferenceChoice", "SettingRecord", "SettingsReset", "SettingsSet",
           "TargetKind", "ValueShape",
           "group_target", "parse_group_target", "parse_variant_target", "variant_target",
           "OutputSwitch", "Reference", "ReferenceChanged", "ReferenceMark",
           "GroupsResolveReply", "GroupsResolveRequest"]
