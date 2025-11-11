from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, List, Tuple

import requests
from zoneinfo import ZoneInfo


MOSCOW_TZ = ZoneInfo("Europe/Moscow")


@dataclass(slots=True)
class Candle:
    timestamp: datetime
    close: float


class IMOEXFetcher:
    BASE_URL = "https://iss.moex.com/iss"

    def __init__(self, board: str, security: str) -> None:
        self.board = board
        self.security = security
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "imoex-bot/1.0"})

    def fetch_last_value(self) -> Tuple[datetime, float]:
        url = f"{self.BASE_URL}/engines/stock/markets/index/boards/{self.board}/securities.json"
        params = {"securities": self.security, "iss.meta": "off"}
        response = self._session.get(url, params=params, timeout=10)
        response.raise_for_status()
        payload = response.json()

        columns = payload["marketdata"]["columns"]
        data_rows = payload["marketdata"]["data"]
        if not data_rows:
            raise RuntimeError("No market data received from MOEX ISS")

        try:
            secid_idx = columns.index("SECID")
            last_idx = columns.index("LAST")
            time_idx = columns.index("SYSTIME")
        except ValueError as exc:
            raise RuntimeError("Unexpected response structure from MOEX ISS") from exc

        matching_rows: Iterable[List] = (
            row for row in data_rows if row[secid_idx] == self.security
        )
        try:
            row = next(iter(matching_rows))
        except StopIteration as exc:
            raise RuntimeError(f"Security {self.security} not found in response") from exc

        last_value = row[last_idx]
        systime_raw = row[time_idx]
        if last_value is None:
            raise RuntimeError("LAST value is missing in ISS response")
        if systime_raw is None:
            timestamp = datetime.now(timezone.utc)
        else:
            systime_clean = systime_raw.replace("T", " ")
            dt = datetime.fromisoformat(systime_clean)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=MOSCOW_TZ)
            timestamp = dt.astimezone(timezone.utc)

        return timestamp, float(last_value)

    def fetch_candles(
        self, start: datetime, end: datetime, interval: int = 1
    ) -> List[Candle]:
        url = (
            f"{self.BASE_URL}/engines/stock/markets/index/securities/{self.security}/candles.json"
        )

        def to_param(dt: datetime) -> str:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(MOSCOW_TZ).strftime("%Y-%m-%d %H:%M:%S")

        params = {
            "from": to_param(start),
            "till": to_param(end),
            "interval": interval,
            "iss.meta": "off",
        }

        candles: List[Candle] = []
        start_offset = 0
        while True:
            page_params = {**params, "start": start_offset}
            response = self._session.get(url, params=page_params, timeout=10)
            response.raise_for_status()
            payload = response.json()

            columns = payload["candles"]["columns"]
            data_rows = payload["candles"]["data"]
            if not data_rows:
                break

            begin_idx = columns.index("begin")
            close_idx = columns.index("close")

            for row in data_rows:
                ts = datetime.strptime(row[begin_idx], "%Y-%m-%d %H:%M:%S")
                ts = ts.replace(tzinfo=MOSCOW_TZ).astimezone(timezone.utc)
                close = float(row[close_idx])
                candles.append(Candle(timestamp=ts, close=close))

            if len(data_rows) < 100:
                break
            start_offset += len(data_rows)

        candles.sort(key=lambda candle: candle.timestamp)
        # Only keep candles inside the requested time window
        return [
            candle
            for candle in candles
            if start <= candle.timestamp <= end + timedelta(minutes=interval)
        ]


__all__ = ["IMOEXFetcher", "Candle", "MOSCOW_TZ"]
