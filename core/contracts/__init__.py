"""Messages that cross a module border, described once.

A producer builds its payload with one of these models and a consumer reads it with the same one,
so the two cannot drift apart unnoticed. Modules import their contracts from here, never from each other.
"""
from .apps import AppCard, AppResults, AppSearchReply, AppSearchRequest
from .alignment import (AlignmentJob, DubResult, JobKind, JobStatus, ReverseEpisode, ReverseReply, ReverseSubmit, SubmitOutcome,
                        SubmitReply, job_dir, job_key, series_of_job_key)
from .composition import (CompositionChanged, CompositionFailed, CompositionGetRequest, CompositionPreviewReply,
                          CompositionPreviewRequest, CompositionReply, PreviewLine, SeasonVoiceOrder, VoiceOrderSet, VoiceSpan, VoiceTitleSet,
                          CompositionRow, EpisodeRef, Exclusion, PlayerSummary, SourceStatus, SourceSummary)
from .compilation import (Absence, CompilationEpisode, CompilationRef, CompilationView, compilation_key,
                          parse_compilation_key)
from .downloads import (STAGES, AttemptLine, DownloadCard, DownloadNeed, DownloadVariant, Figures, StageView)
from .packing import PACK_STAGES, JournalStep, PackCard, PackFigures, PackJournal, PackStageView, PackTrack
from .conform_settings import ConformDomain, ConformParams, ConformSettingsDraft, ConformSettingsSaved, ConformSettingsView
from .provider_settings import ProviderApp, ProviderSaved, ProviderSettingsDraft, ProviderView
from .output_settings import (COMPILATION_MEMBER, COMPILATION_TAKE, LEVER_FORMS, MARKUP_ALLOW, MARKUP_ORDER, MARKUP_SAME, REFERENCE_ALLOW, REFERENCE_ORDER, REFERENCE_SAME, VOICE_ALLOW, VOICE_ORDER, VOICE_TAKE, GroupQueue, GroupVariant, PlayerVariant, Queue, QueueRow, QueueView, QueuesGet, QueuesReply, EffectiveLever, EpisodeSettings, LeverSpec,
                              Level, LevelSettings, SettingRecord, SettingsReset, SettingsSet, SettingsSetMany,
                              TargetKind, ValueShape, group_target, parse_group_target, parse_variant_target, variant_target)
from .reference import CutPicture, OutputSwitch, Reference, ReferenceChanged, ReferenceMark
from .release_table import (LINE_FIELDS, LINE_SEPARATOR, ParsedDomains, PlayerFacts, PlayerPlace, PlayersListReply,
                            PlayersListRequest, ReleaseTable, RowState, RowVoice,
                            SiteResolveReply, SiteResolveRequest, SourcesSettingsView, TrackerMirrors,
                            SeriesReadProgress, TableFailed, TableGetReply, TableGetRequest, TableProgress, TableReady, TableRow,
                            TableState, UnreadPart, line_of, place_of)
from .rules import RuleLine, RuleResult, RulesApplyReply, RulesApplyRequest, RulesProfileChanged
from .series_sources import SeriesKind, SeriesSource, SourceChanged, SourceEvent, SourceType
from .voice_groups import GroupsResolveReply, GroupsResolveRequest
from .availability import Action, NotAllowed, allowed, refusal, require, variant_state
from .work import RUN_CHANGED, WORK_STATE, WORK_WITHDRAWN, RunChanged, RunSnapshot, Slot, WorkState, WorkWithdrawn, slot_of
from .voices import (DubEvent, DubEventStatus, DubHint, DubItem, DubRecord, DubsScope, DubsSnapshot, DubState, DubStatus,
                     Reason, SettingProblem, SnapshotItem,
                     VoiceSettingsDraft, VoiceSettingsSaved, VoiceSettingsView)

__all__ = ["AppCard", "AppResults", "AppSearchReply", "AppSearchRequest", "PACK_STAGES", "JournalStep", "PackCard", "PackFigures", "PackJournal", "PackStageView", "PackTrack", "STAGES", "AttemptLine", "DownloadCard", "DownloadNeed", "DownloadVariant", "Figures", "StageView", "Absence", "CompilationEpisode", "CompilationRef", "CompilationView", "compilation_key", "parse_compilation_key",
           "COMPILATION_MEMBER", "COMPILATION_TAKE", "Action", "NotAllowed", "allowed", "refusal", "require", "variant_state", "LEVER_FORMS", "WORK_WITHDRAWN", "WorkWithdrawn", "RUN_CHANGED", "WORK_STATE", "RunChanged", "RunSnapshot", "Slot", "WorkState", "slot_of", "AlignmentJob", "DubResult", "JobKind", "JobStatus", "ReverseEpisode", "ReverseReply", "ReverseSubmit", "SubmitOutcome", "SubmitReply", "job_dir", "job_key", "series_of_job_key", "DubEvent",
           "DubEventStatus", "DubHint", "DubItem", "DubRecord", "DubsSnapshot", "DubState", "DubStatus", "Reason", "SnapshotItem", "SettingProblem", "VoiceSettingsDraft",
           "VoiceSettingsSaved", "VoiceSettingsView", "DubsScope", "ConformSettingsDraft", "ConformSettingsSaved",
           "ConformSettingsView", "ConformDomain", "ConformParams", "ProviderApp", "ProviderSaved", "ProviderSettingsDraft", "ProviderView",
           "LINE_SEPARATOR", "ParsedDomains", "PlayerFacts", "PlayerPlace", "PlayersListReply", "PlayersListRequest", "SourcesSettingsView", "TrackerMirrors", "ReleaseTable", "RowState", "RowVoice", "SiteResolveReply", "SiteResolveRequest", "TableFailed", "TableGetReply", "TableGetRequest",
           "SeriesReadProgress", "TableProgress", "TableReady", "TableRow", "TableState", "UnreadPart", "line_of", "place_of", "LINE_FIELDS",
           "RuleLine", "RuleResult", "RulesApplyReply", "RulesApplyRequest", "RulesProfileChanged",
           "SeriesKind", "SeriesSource", "SourceChanged", "SourceEvent", "SourceType",
           "CompositionChanged", "CompositionFailed", "CompositionGetRequest", "CompositionPreviewReply", "CompositionPreviewRequest",
           "CompositionReply", "CompositionRow", "PreviewLine", "SeasonVoiceOrder", "VoiceOrderSet", "VoiceSpan", "VoiceTitleSet",
           "EpisodeRef", "Exclusion", "PlayerSummary", "SourceStatus", "SourceSummary",
           "MARKUP_ALLOW", "MARKUP_ORDER", "MARKUP_SAME", "REFERENCE_ALLOW", "REFERENCE_ORDER", "REFERENCE_SAME", "VOICE_ALLOW", "VOICE_ORDER", "VOICE_TAKE", "GroupQueue", "GroupVariant", "PlayerVariant", "Queue", "QueueRow", "QueueView", "QueuesGet", "QueuesReply", "EffectiveLever", "EpisodeSettings", "LeverSpec", "Level", "LevelSettings", "SettingRecord", "SettingsReset", "SettingsSet", "SettingsSetMany",
           "TargetKind", "ValueShape",
           "group_target", "parse_group_target", "parse_variant_target", "variant_target",
           "CutPicture", "OutputSwitch", "Reference", "ReferenceChanged", "ReferenceMark",
           "GroupsResolveReply", "GroupsResolveRequest"]

from .planner import (PlanAsset, PlanAssetTrack, PlanAssetVideo, PlanCommand, PlanDelete, PlanEpisode,  # noqa: E402
                      PlanFailure, PlanForget, PlanGoalTrack, PlanGoalVideo, PlannerView, PlanProblem, PlanRowRef, PlanSnapshot,
                      SyncScope)

__all__ += ["PlanAsset", "PlanAssetTrack", "PlanAssetVideo", "PlanCommand", "PlanDelete", "PlanEpisode", "PlanFailure",
            "PlanProblem", "PlanForget", "SyncScope",
            "PlanGoalTrack", "PlanGoalVideo", "PlannerView", "PlanRowRef", "PlanSnapshot"]
