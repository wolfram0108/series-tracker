"""Модуль catalog — владелец таблицы series и агрегатор статусов (Р-11).

Events (входящие):
  series.status.contribution {source, series_id, flags} — свёртка
      модуля-владельца задач; публикуется им при старте (после своего
      reconcile) и при каждом изменении вклада. Все false = вклад снят.
  gateway.sse.clients {count} — счётчик SSE-подключений; при 0 все
      эфемерные viewing сбрасываются (вкладка не может держать модалку
      без живого SSE).

Commands:
  catalog.viewing.start {series_id} / catalog.viewing.stop {series_id}
      — открытие/закрытие модалки (действия пользователя).

Queries:
  catalog.series.list           → [{...series, statuses: [...]}]
  catalog.series.get {series_id} → {...series, statuses: [...]}
  catalog.status.get {series_id} → {series_id, statuses: [...]}

Events (исходящие):
  series.status.changed {series_id, statuses} — только при изменении
      набора; на этапе 5 транслируется в SSE series_updated (оживляет
      ветку фронта s.statuses, находка 24).
"""
from __future__ import annotations

from core import BaseModule
from core.db import Database
from core.envelope import Envelope

from .aggregator import StatusAggregator
from .repository import CatalogRepository


class CatalogModule(BaseModule):
    name = "catalog"

    def __init__(self, bus, db: Database) -> None:
        self.repo = CatalogRepository(db)
        self.agg = StatusAggregator()
        super().__init__(bus)

    def register(self) -> None:
        self.handle("series.status.contribution", self.on_contribution)
        self.handle("gateway.sse.clients", self.on_sse_clients)
        self.handle("catalog.viewing.start", self.on_viewing_start)
        self.handle("catalog.viewing.stop", self.on_viewing_stop)
        self.handle("catalog.series.list", self.on_series_list)
        self.handle("catalog.series.get", self.on_series_get)
        self.handle("catalog.status.get", self.on_status_get)
        self.handle("catalog.series.touch_scan_time", self.on_touch)

    # --- статусы: вклады и viewing ------------------------------------------

    async def on_contribution(self, env: Envelope) -> None:
        p = env.payload
        changed = self.agg.set_contribution(
            p["series_id"], p["source"], p["flags"])
        self._publish_if_changed(p["series_id"], changed)

    async def on_viewing_start(self, env: Envelope) -> None:
        series_id = env.payload["series_id"]
        self._publish_if_changed(series_id, self.agg.set_viewing(series_id, True))

    async def on_viewing_stop(self, env: Envelope) -> None:
        series_id = env.payload["series_id"]
        self._publish_if_changed(series_id, self.agg.set_viewing(series_id, False))

    async def on_sse_clients(self, env: Envelope) -> None:
        if env.payload["count"] > 0:
            return
        changes = self.agg.clear_all_viewing()
        for series_id, statuses in changes.items():
            self.log.info("SSE-клиентов нет — сброшен viewing для серии %d",
                          series_id)
            self._publish_if_changed(series_id, statuses)

    def _publish_if_changed(self, series_id: int,
                            statuses: list[str] | None) -> None:
        if statuses is None:
            return
        self.publish_event("series.status.changed",
                           {"series_id": series_id, "statuses": statuses})

    # --- queries --------------------------------------------------------------

    async def on_series_list(self, env: Envelope) -> list[dict]:
        rows = await self.repo.all_series()
        for row in rows:
            row["statuses"] = self.agg.statuses(row["id"])
        return rows

    async def on_series_get(self, env: Envelope) -> dict:
        series_id = env.payload["series_id"]
        row = await self.repo.get_series(series_id)
        if row is None:
            raise LookupError(f"сериал {series_id} не найден")
        row["statuses"] = self.agg.statuses(series_id)
        return row

    async def on_status_get(self, env: Envelope) -> dict:
        series_id = env.payload["series_id"]
        return {"series_id": series_id, "statuses": self.agg.statuses(series_id)}

    async def on_touch(self, env: Envelope) -> None:
        """Обновление last_scan_time («время жизни» карточки) после
        успешной загрузки/обработки — поведение старой системы."""
        await self.repo.touch_scan_time(env.payload["series_id"])
