from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
from typing import Sequence, Tuple

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt


def build_chart(points: Sequence[Tuple[datetime, float]]) -> BytesIO:
    if not points:
        raise ValueError("At least one data point is required to build a chart")

    times = [ts.astimezone(timezone.utc) for ts, _ in points]
    values = [value for _, value in points]

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(times, values, color="#003f5c", linewidth=2)

    min_value = min(values)
    max_value = max(values)
    padding = max(1.0, (max_value - min_value) * 0.05)
    ax.set_ylim(min_value - padding, max_value + padding)

    ax.set_title("IMOEX2 за последние 5 часов", fontsize=14)
    ax.set_ylabel("Значение индекса")
    ax.grid(True, linestyle="--", alpha=0.4)

    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.xaxis.set_major_locator(mdates.AutoDateLocator(maxticks=8))
    fig.autofmt_xdate()
    fig.tight_layout()

    buffer = BytesIO()
    fig.savefig(buffer, format="png", dpi=200)
    plt.close(fig)
    buffer.seek(0)
    return buffer


__all__ = ["build_chart"]
