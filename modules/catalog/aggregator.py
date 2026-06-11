"""Агрегатор статусов сериала (Р-11): чистая логика без шины и БД.

Статус сериала — вычисляемое значение: объединение «свёрток» (вкладов)
модулей-владельцев задач плюс эфемерный viewing. Ничего не хранится в
БД; вклады живут в памяти и восстанавливаются публикациями поставщиков
при старте процесса.

Иерархия повторяет старый STATUS_HIERARCHY — это источник порядка
пилюль во фронте (дизайн-контракт): первый в списке рисуется первым.
"""
from __future__ import annotations

HIERARCHY = [
    "error", "scanning", "checking", "slicing",
    "renaming", "metadata", "activating",
    "downloading", "ready", "viewing", "waiting",
]

# viewing и waiting в свёртках не запрещены умышленно: waiting — часть
# VK-свёртки (семантика «есть pending в плане»), viewing же приходит
# только командами и в свёртках недопустим.
ALLOWED_FLAGS = frozenset(HIERARCHY) - {"viewing"}


class StatusAggregator:
    def __init__(self) -> None:
        # series_id -> source -> множество активных флагов
        self._contrib: dict[int, dict[str, frozenset[str]]] = {}
        self._viewing: set[int] = set()
        # последний отданный наружу набор; None = ещё не публиковался
        self._published: dict[int, tuple[str, ...]] = {}

    # --- входящие изменения ------------------------------------------------

    def set_contribution(self, series_id: int, source: str,
                         flags: dict[str, bool]) -> list[str] | None:
        """Принять свёртку источника. Возвращает новый набор статусов,
        если он изменился, иначе None."""
        unknown = set(flags) - ALLOWED_FLAGS
        if unknown:
            raise ValueError(
                f"недопустимые флаги в свёртке '{source}': {sorted(unknown)}")
        active = frozenset(name for name, on in flags.items() if on)
        per_series = self._contrib.setdefault(series_id, {})
        if active:
            per_series[source] = active
        else:
            per_series.pop(source, None)  # все false — вклад снят
            if not per_series:
                self._contrib.pop(series_id, None)
        return self._diff(series_id)

    def set_viewing(self, series_id: int, on: bool) -> list[str] | None:
        if on:
            self._viewing.add(series_id)
        else:
            self._viewing.discard(series_id)
        return self._diff(series_id)

    def clear_all_viewing(self) -> dict[int, list[str]]:
        """Сброс viewing у всех (последний SSE-клиент отключился).
        Возвращает изменившиеся наборы по сериалам."""
        affected, self._viewing = self._viewing, set()
        changes = {}
        for series_id in affected:
            statuses = self._diff(series_id)
            if statuses is not None:
                changes[series_id] = statuses
        return changes

    def forget(self, series_id: int) -> None:
        """Сериал удалён — забыть его вклады."""
        self._contrib.pop(series_id, None)
        self._viewing.discard(series_id)
        self._published.pop(series_id, None)

    # --- чтение --------------------------------------------------------------

    def statuses(self, series_id: int) -> list[str]:
        active: set[str] = set()
        for flags in self._contrib.get(series_id, {}).values():
            active |= flags
        # waiting определяется ДО viewing: просмотр — не активность
        # (семантика оригинала: «viewing, waiting» при открытой модалке
        # бездействующего сериала).
        if not active:
            active = {"waiting"}
        if series_id in self._viewing:
            active.add("viewing")
        return sorted(active, key=HIERARCHY.index)

    # --- внутреннее ----------------------------------------------------------

    def _diff(self, series_id: int) -> list[str] | None:
        current = tuple(self.statuses(series_id))
        if self._published.get(series_id, ("waiting",)) == current:
            return None
        self._published[series_id] = current
        return list(current)
