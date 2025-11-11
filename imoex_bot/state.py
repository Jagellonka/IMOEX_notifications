from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, List, Tuple


@dataclass
class BotState:
    price_message_id: int | None = None
    chart_message_id: int | None = None
    history: List[Tuple[str, float]] = field(default_factory=list)

    def prune_history(self, max_age: timedelta) -> None:
        cutoff = datetime.now(timezone.utc) - max_age
        self.history = [
            (ts_str, value)
            for ts_str, value in self.history
            if self._to_datetime(ts_str) >= cutoff
        ]

    def append_history_point(self, timestamp: datetime, value: float) -> None:
        aware_ts = timestamp.astimezone(timezone.utc)
        iso_value = aware_ts.isoformat()
        if self.history and self.history[-1][0] == iso_value:
            self.history[-1] = (iso_value, value)
        else:
            self.history.append((iso_value, value))

    def last_value(self) -> float | None:
        if not self.history:
            return None
        return self.history[-1][1]

    def iter_points(self) -> Iterable[Tuple[datetime, float]]:
        for ts_str, value in self.history:
            yield self._to_datetime(ts_str), value

    @staticmethod
    def _to_datetime(ts_str: str) -> datetime:
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)


class StateStorage:
    def __init__(self, path: Path):
        self._path = path
        self._state = BotState()
        self._load()

    @property
    def state(self) -> BotState:
        return self._state

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            with self._path.open("r", encoding="utf-8") as fp:
                raw = json.load(fp)
        except (OSError, json.JSONDecodeError):
            return

        history_raw = raw.get("history", [])
        history: List[Tuple[str, float]] = []
        for entry in history_raw:
            if not isinstance(entry, (list, tuple)) or len(entry) != 2:
                continue
            ts, value = entry
            if not isinstance(ts, str):
                continue
            try:
                float(value)
            except (TypeError, ValueError):
                continue
            history.append((ts, float(value)))

        self._state = BotState(
            price_message_id=raw.get("price_message_id"),
            chart_message_id=raw.get("chart_message_id"),
            history=history,
        )

    def save(self) -> None:
        data = {
            "price_message_id": self._state.price_message_id,
            "chart_message_id": self._state.chart_message_id,
            "history": self._state.history,
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("w", encoding="utf-8") as fp:
            json.dump(data, fp, ensure_ascii=False, indent=2)


__all__ = ["BotState", "StateStorage"]
