from __future__ import annotations

import logging
import sys

from telegram.ext import Application

from imoex_bot.config import Settings, SettingsError
from imoex_bot.service import IMOEXBotService


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    try:
        settings = Settings.from_env()
    except SettingsError as exc:  # pragma: no cover - start-up validation
        logging.error("%s", exc)
        sys.exit(1)

    service = IMOEXBotService(settings)
    application = Application.builder().token(settings.bot_token).build()
    application.post_init = service.post_init
    application.post_shutdown = service.post_shutdown
    application.run_polling(stop_signals=None)


if __name__ == "__main__":
    main()
