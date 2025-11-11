from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from io import BytesIO
from typing import List, Tuple

from telegram import InputFile, InputMediaPhoto
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import Application, ContextTypes

from .config import Settings
from .data_fetcher import IMOEXFetcher
from .graph import build_chart
from .state import StateStorage

logger = logging.getLogger(__name__)


class IMOEXBotService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.storage = StateStorage(settings.state_path)
        self.fetcher = IMOEXFetcher(settings.board, settings.security)
        self._lock = asyncio.Lock()
        self._alert_lock = asyncio.Lock()
        self._alert_history: List[Tuple[datetime, float]] = []

    async def post_init(self, application: Application) -> None:
        await self._prepare_history()
        await self._ensure_messages(application)
        await self._update_price_message(
            "⏳ Инициализация…", application
        )
        await self._update_chart_message(application)

        job_queue = application.job_queue
        job_queue.run_repeating(
            self._price_job,
            interval=self.settings.price_update_interval,
            first=0.0,
            name="price-updater",
        )
        job_queue.run_repeating(
            self._chart_job,
            interval=self.settings.chart_update_interval,
            first=self.settings.chart_update_interval,
            name="chart-updater",
        )

    async def post_shutdown(self, application: Application) -> None:  # pragma: no cover
        self.storage.save()

    async def _prepare_history(self) -> None:
        state = self.storage.state
        state.prune_history(timedelta(hours=6))
        if state.history:
            logger.info("Loaded %d history points from state", len(state.history))
            return

        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(hours=5)
        try:
            candles = await asyncio.to_thread(
                self.fetcher.fetch_candles, start_time, end_time
            )
        except Exception:
            logger.exception("Failed to fetch initial candle history")
            return

        for candle in candles:
            state.append_history_point(candle.timestamp, candle.close)
        self.storage.save()
        logger.info("Fetched %d historical candles", len(candles))

    async def _ensure_messages(self, application: Application) -> None:
        state = self.storage.state
        bot = application.bot
        chat_id = self.settings.chat_id

        if state.price_message_id is not None:
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=state.price_message_id,
                    text="♻️ Перезапуск отслеживания…",
                )
            except TelegramError:
                logger.warning("Stored price message is not accessible, creating new one")
                state.price_message_id = None

        if state.price_message_id is None:
            message = await bot.send_message(
                chat_id=chat_id,
                text="♻️ Перезапуск отслеживания…",
            )
            state.price_message_id = message.message_id
            await self._pin_message(bot, chat_id, message.message_id)
            self.storage.save()

        if state.chart_message_id is not None:
            try:
                await bot.edit_message_caption(
                    chat_id=chat_id,
                    message_id=state.chart_message_id,
                    caption="Обновляю график…",
                )
            except TelegramError:
                logger.warning("Stored chart message is not accessible, creating new one")
                state.chart_message_id = None

        if state.chart_message_id is None:
            placeholder = await self._build_placeholder_chart()
            message = await bot.send_photo(
                chat_id=chat_id,
                photo=placeholder,
                caption="Обновляю график…",
            )
            state.chart_message_id = message.message_id
            await self._pin_message(bot, chat_id, message.message_id)
            self.storage.save()

    async def _build_placeholder_chart(self) -> BytesIO:
        points = list(self.storage.state.iter_points())
        if not points:
            now = datetime.now(timezone.utc)
            points = [(now - timedelta(minutes=5), 0.0), (now, 0.0)]
        else:
            last_ts, last_value = points[-1]
            start_ts = max(points[0][0], last_ts - timedelta(minutes=5))
            points = [
                (ts, value)
                for ts, value in points
                if start_ts <= ts <= last_ts
            ] or [(last_ts - timedelta(minutes=5), last_value), (last_ts, last_value)]
        return await asyncio.to_thread(build_chart, points)

    async def _pin_message(self, bot, chat_id: int, message_id: int) -> None:
        try:
            await bot.pin_chat_message(chat_id=chat_id, message_id=message_id, disable_notification=True)
        except TelegramError:
            logger.exception("Failed to pin message %s", message_id)

    async def _price_job(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            timestamp, value = await asyncio.to_thread(self.fetcher.fetch_last_value)
        except Exception:
            logger.exception("Failed to fetch last price from MOEX")
            return

        async with self._lock:
            state = self.storage.state
            state.append_history_point(timestamp, value)
            state.prune_history(timedelta(hours=6))
            self.storage.save()

            await self._update_price_message(self._format_price_message(timestamp, value), context.application)
            await self._handle_alert(timestamp, value, context.application)

    async def _chart_job(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        async with self._lock:
            try:
                await self._update_chart_message(context.application)
            except Exception:
                logger.exception("Failed to update chart message")

    async def _update_price_message(self, text: str, application: Application) -> None:
        message_id = self.storage.state.price_message_id
        if message_id is None:
            return
        try:
            await application.bot.edit_message_text(
                chat_id=self.settings.chat_id,
                message_id=message_id,
                text=text,
                parse_mode=ParseMode.HTML,
            )
        except TelegramError:
            logger.exception("Failed to edit price message")

    async def _update_chart_message(self, application: Application) -> None:
        state = self.storage.state
        message_id = state.chart_message_id
        if message_id is None:
            return

        points = list(state.iter_points())
        if not points:
            return
        cutoff = datetime.now(timezone.utc) - timedelta(hours=5)
        recent_points = [(ts, value) for ts, value in points if ts >= cutoff]
        if not recent_points:
            recent_points = points[-2:]

        chart = await asyncio.to_thread(build_chart, recent_points)
        media = InputMediaPhoto(
            media=InputFile(chart, filename="imoex_chart.png"),
            caption=self._build_chart_caption(recent_points),
        )
        try:
            await application.bot.edit_message_media(
                chat_id=self.settings.chat_id,
                message_id=message_id,
                media=media,
            )
        except TelegramError:
            logger.exception("Failed to edit chart message")

    def _build_chart_caption(self, points: List[Tuple[datetime, float]]) -> str:
        start_time = points[0][0].astimezone(MOSCOW_TZ)
        end_time = points[-1][0].astimezone(MOSCOW_TZ)
        return (
            f"Диапазон: {start_time:%d.%m %H:%M} – {end_time:%d.%m %H:%M} (МСК)\n"
            f"Количество точек: {len(points)}"
        )

    def _format_price_message(self, timestamp: datetime, value: float) -> str:
        history_points = list(self.storage.state.iter_points())
        cutoff = timestamp - timedelta(seconds=self.settings.alert_window)
        past_values = [val for ts, val in history_points if ts <= cutoff]
        difference_text = ""
        if past_values:
            reference = past_values[-1]
            diff = value - reference
            if abs(diff) >= 0.01:
                arrow = "⬆️" if diff > 0 else "⬇️"
                difference_text = f"\n{arrow} Изменение за минуту: {diff:+.2f}"

        moscow_time = timestamp.astimezone(MOSCOW_TZ)
        return (
            f"<b>{self.settings.index_name}</b>\n"
            f"Текущее значение: <b>{value:.2f}</b>\n"
            f"Время обновления: {moscow_time:%d.%m.%Y %H:%M:%S %Z}{difference_text}"
        )

    async def _handle_alert(
        self, timestamp: datetime, value: float, application: Application
    ) -> None:
        async with self._alert_lock:
            self._alert_history.append((timestamp, value))
            cutoff = timestamp - timedelta(seconds=self.settings.alert_window)
            self._alert_history = [
                (ts, val) for ts, val in self._alert_history if ts >= cutoff
            ]
            if not self._alert_history:
                return
            earliest_ts, earliest_value = self._alert_history[0]
            diff = value - earliest_value
            if abs(diff) < self.settings.alert_threshold:
                return

            direction = "Рост" if diff > 0 else "Падение"
            arrow = "🚀" if diff > 0 else "📉"
            message = await application.bot.send_message(
                chat_id=self.settings.chat_id,
                text=(
                    f"{arrow} {direction} индекса на {diff:+.2f} пунктов за минуту!\n"
                    f"Текущее значение: {value:.2f}"
                ),
            )
            asyncio.create_task(self._delete_message_later(application, message.message_id))
            self._alert_history.clear()

    async def _delete_message_later(self, application: Application, message_id: int) -> None:
        await asyncio.sleep(3600)
        try:
            await application.bot.delete_message(
                chat_id=self.settings.chat_id, message_id=message_id
            )
        except TelegramError:
            logger.warning("Failed to delete alert message %s", message_id)


from .data_fetcher import MOSCOW_TZ  # noqa: E402

__all__ = ["IMOEXBotService"]
