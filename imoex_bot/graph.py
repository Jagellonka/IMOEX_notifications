from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO
from typing import Sequence, Tuple

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import matplotlib.patches as patches
from matplotlib.axes import Axes

from .data_fetcher import DaySummary

MOSCOW_TZ = timezone(timedelta(hours=3))


def build_chart(
    points: Sequence[Tuple[datetime, float]], summary: DaySummary | None = None
) -> BytesIO:
    if not points:
        raise ValueError("At least one data point is required to build a chart")

    times = [ts.astimezone(MOSCOW_TZ) for ts, _ in points]
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
    if summary is not None:
        _add_summary_inset(ax, summary)

    fig.tight_layout()

    buffer = BytesIO()
    fig.savefig(buffer, format="png", dpi=200)
    plt.close(fig)
    buffer.seek(0)
    return buffer


def _add_summary_inset(ax: Axes, summary: DaySummary) -> None:
    inset = ax.inset_axes([0.63, 0.08, 0.33, 0.42])
    inset.set_facecolor("#f8f9fb")
    inset.set_xticks([])
    inset.set_yticks([])
    inset.set_xlim(0, 1)
    inset.set_ylim(0, 1)

    for spine in inset.spines.values():
        spine.set_edgecolor("#003f5c")
        spine.set_linewidth(0.8)
        spine.set_alpha(0.4)

    inset.text(
        0.5,
        0.95,
        "Дневная свеча",
        ha="center",
        va="center",
        fontsize=10,
        fontweight="semibold",
        color="#003f5c",
    )

    high = summary.high
    low = summary.low
    open_ = summary.open
    close = summary.close
    body_color = "#0b8a6a" if close >= open_ else "#d64545"

    spread = max(high - low, 1e-6)

    def scale(value: float) -> float:
        return 0.15 + 0.7 * (value - low) / spread

    wick_y_bottom = scale(low)
    wick_y_top = scale(high)
    inset.plot(
        [0.2, 0.2],
        [wick_y_bottom, wick_y_top],
        color=body_color,
        linewidth=2,
        solid_capstyle="round",
    )

    body_bottom = scale(min(open_, close))
    body_height = scale(max(open_, close)) - body_bottom
    if body_height < 0.02:
        body_height = 0.02
    candle_body = patches.Rectangle(
        (0.2 - 0.06, body_bottom),
        0.12,
        body_height,
        facecolor=body_color,
        edgecolor=body_color,
        linewidth=0,
        alpha=0.9,
        zorder=3,
    )
    inset.add_patch(candle_body)

    labels = [
        ("Открытие", open_),
        ("Текущая", close),
        ("Максимум", high),
        ("Минимум", low),
    ]
    y = 0.78
    for title, value in labels:
        inset.text(
            0.38,
            y,
            title,
            ha="left",
            va="center",
            fontsize=9,
            color="#4a4a4a",
        )
        inset.text(
            0.95,
            y,
            f"{value:.2f}",
            ha="right",
            va="center",
            fontsize=10,
            color="#111",
            fontfamily="DejaVu Sans Mono",
        )
        y -= 0.18


__all__ = ["build_chart"]
