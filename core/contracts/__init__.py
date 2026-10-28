"""Messages that cross a module border, described once.

A producer builds its payload with one of these models and a consumer reads it with the same one,
so the two cannot drift apart unnoticed. Modules import their contracts from here, never from each other.
"""
from .alignment import AlignmentJob, DubResult, JobStatus
from .release import Release, ReleaseSource, ReleaseTitle
from .voices import (DubEvent, DubEventStatus, DubHint, DubItem, DubRecord, DubsSnapshot, DubState, DubStatus,
                     Layout, LayoutVoice, Reason, SnapshotItem)

__all__ = ["AlignmentJob", "DubResult", "JobStatus", "Release", "ReleaseSource", "ReleaseTitle", "DubEvent",
           "DubEventStatus", "DubHint", "DubItem", "DubRecord", "DubsSnapshot", "DubState", "DubStatus", "Layout",
           "LayoutVoice", "Reason", "SnapshotItem"]
