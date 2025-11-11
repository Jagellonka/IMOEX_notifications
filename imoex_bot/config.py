from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os


class SettingsError(RuntimeError):
    """Raised when required settings are missing."""


@dataclass(slots=True)
class Settings:
    bot_token: str
    state_path: Path = Path("bot_state.json")
    security: str = "IMOEX2"
    board: str = "SNDX"
    index_name: str = "IMOEX2 (все сессии)"
    price_update_interval: float = 1.0
    chart_update_interval: float = 300.0
    alert_threshold: float = 15.0
    alert_window: float = 60.0

    @classmethod
    def from_env(cls) -> "Settings":
        token = (
            os.getenv("TELEGRAM_BOT_TOKEN")
            or os.getenv("BOT_TOKEN")
            or os.getenv("TG_BOT_TOKEN")
        )
        if not token:
            raise SettingsError(
                "Bot token is not provided. Set TELEGRAM_BOT_TOKEN environment variable."
            )

        state_path = Path(os.getenv("BOT_STATE_PATH", "bot_state.json"))
        return cls(bot_token=token, state_path=state_path)


__all__ = ["Settings", "SettingsError"]
