from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.bot import DefaultBotProperties
from aiogram.enums import ParseMode

from imoex_bot.config import Settings, SettingsError
from imoex_bot.service import IMOEXBotService


async def _run_bot(settings: Settings) -> None:
    service = IMOEXBotService(settings)

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()

    dp.startup.register(service.on_startup)
    dp.shutdown.register(service.on_shutdown)

    await dp.start_polling(bot)


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

    asyncio.run(_run_bot(settings))


if __name__ == "__main__":
    main()
