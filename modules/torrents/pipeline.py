"""Стадийная машина торрент-конвейера — чистая решающая функция.

ИНВАРИАНТ ЯДРА (зафиксирован пользователем, Р-14): торрент стоит на
паузе с момента добавления и до завершения переименования. Единственное
исключение — magnet: списка файлов нет, пока не скачаны метаданные,
поэтому торрент запускается РОВНО на время их получения и немедленно
ставится обратно на паузу. Любая закачка до переименования кладёт файлы
под исходными именами и ломает зачёт уже существующих (recheck).

Зачем recheck: при замене раздачи файлы старой уже лежат на диске под
итоговыми именами; новый торрент переименовывается под те же имена, и
recheck засчитывает совпавшие куски — докачивается только новое.

Значения стадий в БД — старые строки (констрейнт данных + карта
стадия→статус из Р-11); отличие от оригинала одно: переименование
выполняется на стадии 'renaming' (раньше — пустышка, а работа делалась
в конце 'awaiting_pause_before_rename'); поведение идентично, имена
честные.
"""
from __future__ import annotations

# Стадии (значения в agent_tasks.stage)
AWAITING_METADATA = "awaiting_metadata"          # magnet: запустить
POLLING_FOR_SIZE = "polling_for_size"            # magnet: ждать метаданных
AWAITING_PAUSE = "awaiting_pause_before_rename"  # ждать стабильной паузы
RENAMING = "renaming"                            # переименовать файлы
RECHECKING = "rechecking"                        # зачесть существующее
ACTIVATING = "activating"                        # запустить и завершить
ERROR = "error"                                  # носитель ошибки (Р-11)

INITIAL_STAGE = {"file": AWAITING_PAUSE, "magnet": AWAITING_METADATA}

# Карта стадия → статусный флаг свёртки (старый AGENT_STAGES_TO_FLAG_MAP)
STAGE_FLAGS = {
    AWAITING_METADATA: "metadata",
    POLLING_FOR_SIZE: "metadata",
    AWAITING_PAUSE: "metadata",
    RENAMING: "renaming",
    RECHECKING: "checking",
    ACTIVATING: "activating",
    ERROR: "error",
}

# Семейства состояний qBittorrent (как в оригинале)
STABLE_PAUSED = {"pausedUP", "pausedDL", "stalledUP", "stalledDL",
                 "stoppedUP", "stoppedDL"}  # stopped* — поколение qBit 5.x
CHECKING = {"checkingUP", "checkingDL", "checkingResumeData"}
POST_RECHECK = {"queuedUP", "queuedDL", "stalledUP", "stalledDL",
                "uploading", "downloading", "pausedUP", "pausedDL",
                "stoppedUP", "stoppedDL"}
RUNNING = {"uploading", "stalledUP", "forcedUP", "downloading",
           "stalledDL", "forcedDL", "queuedUP", "queuedDL"}
PAUSED = {"pausedUP", "pausedDL", "stoppedUP", "stoppedDL"}
# Простой: торрент в загрузке, но данные не идут (нет пиров/источников) — статус idle (Р-11).
IDLE = {"stalledDL"}
# Очередь: торрент ждёт свободного слота в очереди qBit — статус queued (Р-11).
QUEUED = {"queuedDL"}
# Состояния, в которых торрент ничего не качает и не проверяет: ни одной пилюли.
NOT_DOWNLOADING = {"pausedUP", "pausedDL", "stoppedUP", "stoppedDL", "uploading"}


def torrent_flag(status: str, complete: bool) -> str | None:
    """The status flag of a series that a torrent gives by what its client says (the one place that words it, for the
    torrents of the pipeline and the releases of the torrent sources): a check is shown whatever the progress; None — the
    torrent is paused or seeding and shows nothing."""
    if status in CHECKING:
        return "checking"
    if complete:
        return "ready"
    if status in IDLE:
        return "idle"
    if status in QUEUED:
        return "queued"
    if status in NOT_DOWNLOADING:
        return None
    return "downloading"


def decide(stage: str, info: dict, recheck_initiated: bool,
           ) -> tuple[str | None, str | None]:
    """(стадия, инфо qBittorrent) -> (действие, следующая стадия).

    Действия исполняет модуль: resume / pause / force_pause / rename /
    recheck / resume_and_complete / complete. (None, None) — ждать.
    """
    state = info.get("state")

    if stage == AWAITING_METADATA:
        # исключение инварианта: запуск ровно до получения метаданных
        return "resume", POLLING_FOR_SIZE

    if stage == POLLING_FOR_SIZE:
        if info.get("total_size", 0) > 0:
            return "pause", AWAITING_PAUSE  # метаданные есть — назад на паузу
        return None, None

    if stage == AWAITING_PAUSE:
        if state in STABLE_PAUSED:
            return None, RENAMING
        if state:  # неожиданно активен — немедленная остановка (инвариант)
            return "force_pause", None
        return None, None

    if stage == RENAMING:
        return "rename", RECHECKING

    if stage == RECHECKING:
        if not recheck_initiated:
            return "recheck", None
        if state in POST_RECHECK and state not in CHECKING:
            return None, ACTIVATING
        return None, None

    if stage == ACTIVATING:
        if state in RUNNING:
            return "complete", None
        if state in PAUSED:
            return "resume_and_complete", None
        return None, None

    raise ValueError(f"неизвестная стадия конвейера: {stage}")
