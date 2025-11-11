from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable, List, Optional, Sequence, Tuple

import requests
from zoneinfo import ZoneInfo


MOSCOW_TZ = ZoneInfo("Europe/Moscow")


@dataclass(slots=True)
class Candle:
    timestamp: datetime
    close: float


@dataclass(slots=True)
class DaySummary:
    open: float
    high: float
    low: float
    close: float


class IMOEXFetcher:
    BASE_URL = "https://iss.moex.com/iss"

    def __init__(self, board: str, security: str) -> None:
        self.board = board
        self.security = security
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "imoex-bot/1.0"})

    @staticmethod
    def _find_column_index(
        columns: Sequence[str],
        candidates: Sequence[str],
        *,
        required: bool = True,
    ) -> Tuple[Optional[int], Optional[str]]:
        for name in candidates:
            try:
                return columns.index(name), name
            except ValueError:
                continue
        if required:
            raise RuntimeError(
                "Unexpected response structure from MOEX ISS: missing columns "
                + ", ".join(candidates)
            )
        return None, None

    @staticmethod
    def _to_moscow_timestamp(value: object) -> datetime:
        if isinstance(value, datetime):
            dt = value
        elif isinstance(value, str) and value.strip():
            systime_clean = value.replace("T", " ").strip()
            try:
                dt = datetime.fromisoformat(systime_clean)
            except ValueError:
                raise RuntimeError(
                    "Unexpected timestamp format received from MOEX ISS"
                ) from None
        else:
            raise RuntimeError("Timestamp value is missing in ISS response")

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=MOSCOW_TZ)
        else:
            dt = dt.astimezone(MOSCOW_TZ)
        return dt

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

        secid_idx, _ = self._find_column_index(columns, ["SECID"])
        last_idx, last_column = self._find_column_index(
            columns,
            [
                "CURRENTVALUE",
                "LAST",
                "LASTVALUE",
                "LASTPRICE",
                "VALUE",
            ],
        )
        time_idx, _ = self._find_column_index(
            columns,
            [
                "SYSTIME",
                "TIME",
                "UPDATETIME",
                "DATETIME",
                "LASTCHANGE",
            ],
            required=False,
        )

        matching_rows: Iterable[List] = (
            row for row in data_rows if row[secid_idx] == self.security
        )
        try:
            row = next(iter(matching_rows))
        except StopIteration as exc:
            raise RuntimeError(f"Security {self.security} not found in response") from exc

        last_value = row[last_idx]
        systime_raw = row[time_idx] if time_idx is not None else None

        if last_value is None:
            raise RuntimeError(
                f"{last_column or 'LAST'} value is missing in ISS response"
            )

        if systime_raw in (None, ""):
            timestamp = datetime.now(MOSCOW_TZ)
        else:
            try:
                timestamp = self._to_moscow_timestamp(systime_raw)
            except RuntimeError:
                timestamp = datetime.now(MOSCOW_TZ)

        return timestamp, float(last_value)

    def fetch_day_summary(self) -> DaySummary:
        url = f"{self.BASE_URL}/engines/stock/markets/index/boards/{self.board}/securities.json"
        params = {"securities": self.security, "iss.meta": "off"}
        response = self._session.get(url, params=params, timeout=10)
        response.raise_for_status()
        payload = response.json()

        columns = payload["marketdata"]["columns"]
        data_rows = payload["marketdata"]["data"]
        if not data_rows:
            raise RuntimeError("No market data received from MOEX ISS")

        secid_idx, _ = self._find_column_index(columns, ["SECID"])
        last_idx, last_column = self._find_column_index(
            columns,
            [
                "CURRENTVALUE",
                "LAST",
                "LASTVALUE",
                "LASTPRICE",
                "VALUE",
            ],
        )
        open_idx, open_column = self._find_column_index(
            columns,
            ["OPEN", "OPENVALUE", "OPENVALUE_RUR", "FIRST"],
            required=False,
        )
        high_idx, high_column = self._find_column_index(
            columns,
            ["HIGH", "HIGHVALUE", "HIGHPRICE"],
            required=False,
        )
        low_idx, low_column = self._find_column_index(
            columns,
            ["LOW", "LOWVALUE", "LOWPRICE"],
            required=False,
        )

        matching_rows: Iterable[List] = (
            row for row in data_rows if row[secid_idx] == self.security
        )
        try:
            row = next(iter(matching_rows))
        except StopIteration as exc:
            raise RuntimeError(f"Security {self.security} not found in response") from exc

        def _pick_value(idx: Optional[int], column_name: Optional[str]) -> float:
            if idx is None:
                raise RuntimeError(
                    "Unexpected response structure from MOEX ISS: missing "
                    f"{column_name or 'value'} column"
                )
            value = row[idx]
            if value is None:
                raise RuntimeError(
                    f"{column_name or 'value'} value is missing in ISS response"
                )
            try:
                return float(value)
            except (TypeError, ValueError) as exc:
                raise RuntimeError(
                    f"{column_name or 'value'} value is not a number in ISS response"
                ) from exc

        last_value = _pick_value(last_idx, last_column)

        try:
            open_value = _pick_value(open_idx, open_column)
        except RuntimeError:
            open_value = last_value

        try:
            high_value = _pick_value(high_idx, high_column)
        except RuntimeError:
            high_value = last_value

        try:
            low_value = _pick_value(low_idx, low_column)
        except RuntimeError:
            low_value = last_value

        return DaySummary(
            open=open_value,
            high=high_value,
            low=low_value,
            close=last_value,
        )

    def fetch_candles(
        self, start: datetime, end: datetime, interval: int = 1
    ) -> List[Candle]:
        url = (
            f"{self.BASE_URL}/engines/stock/markets/index/securities/{self.security}/candles.json"
        )

        def to_param(dt: datetime) -> str:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=MOSCOW_TZ)
            else:
                dt = dt.astimezone(MOSCOW_TZ)
            return dt.strftime("%Y-%m-%d %H:%M:%S")

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
                ts_value = row[begin_idx]
                ts = datetime.fromisoformat(ts_value)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=MOSCOW_TZ)
                else:
                    ts = ts.astimezone(MOSCOW_TZ)
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


__all__ = ["IMOEXFetcher", "Candle", "DaySummary", "MOSCOW_TZ"]
