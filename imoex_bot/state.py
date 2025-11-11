from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

from zoneinfo import ZoneInfo


MOSCOW_TZ = ZoneInfo("Europe/Moscow")

@dataclass
class ChatState:
    price_message_id: int | None = None
    chart_message_id: int | None = None


@dataclass
class BotState:
    history: List[Tuple[str, float]] = field(default_factory=list)
    chats: Dict[int, ChatState] = field(default_factory=dict)

    def prune_history(self, max_age: timedelta) -> None:
        cutoff = datetime.now(MOSCOW_TZ) - max_age
        self.history = [
            (ts_str, value)
            for ts_str, value in self.history
            if self._to_datetime(ts_str) >= cutoff
        ]

    def append_history_point(self, timestamp: datetime, value: float) -> None:
        aware_ts = timestamp.astimezone(MOSCOW_TZ)
        iso_value = aware_ts.isoformat()
        if self.history and self.history[-1][0] == iso_value:
            self.history[-1] = (iso_value, value)
        else:
            self.history.append((iso_value, value))

    def last_value(self) -> float | None:
        if not self.history:
            return None
        return self.history[-1][1]

    def last_point(self) -> Tuple[datetime, float] | None:
        if not self.history:
            return None
        ts_str, value = self.history[-1]
        return self._to_datetime(ts_str), value

    def iter_points(self) -> Iterable[Tuple[datetime, float]]:
        for ts_str, value in self.history:
            yield self._to_datetime(ts_str), value

    def ensure_chat(self, chat_id: int) -> ChatState:
        chat = self.chats.get(chat_id)
        if chat is None:
            chat = ChatState()
            self.chats[chat_id] = chat
        return chat

    def iter_chats(self) -> Iterable[Tuple[int, ChatState]]:
        return self.chats.items()

    @staticmethod
    def _to_datetime(ts_str: str) -> datetime:
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=MOSCOW_TZ)
        return dt.astimezone(MOSCOW_TZ)


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

        chats: Dict[int, ChatState] = {}
        chats_raw = raw.get("chats")
        if isinstance(chats_raw, dict):
            for chat_id_raw, payload in chats_raw.items():
                try:
                    chat_id = int(chat_id_raw)
                except (TypeError, ValueError):
                    continue
                if not isinstance(payload, dict):
                    continue
                chats[chat_id] = ChatState(
                    price_message_id=payload.get("price_message_id"),
                    chart_message_id=payload.get("chart_message_id"),
                )

        self._state = BotState(history=history, chats=chats)

    def save(self) -> None:
        data = {
            "history": self._state.history,
            "chats": {
                str(chat_id): {
                    "price_message_id": chat.price_message_id,
                    "chart_message_id": chat.chart_message_id,
                }
                for chat_id, chat in self._state.chats.items()
            },
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("w", encoding="utf-8") as fp:
            json.dump(data, fp, ensure_ascii=False, indent=2)


__all__ = ["BotState", "ChatState", "StateStorage"]
