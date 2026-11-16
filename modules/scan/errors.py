"""What stops a sync: a failed step, or the deletion of its series."""


class ScanError(RuntimeError):
    pass


class SyncFailed(ScanError):
    """A step of a synchronization failed; the message is a reason (core/reasons), not a sentence."""


class ScanAborted(RuntimeError):
    """The series was deleted during the scan: the work stops."""
