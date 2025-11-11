from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from io import BytesIO
from typing import Any, Coroutine, Dict, Iterable, List, Tuple

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import CommandStart
from aiogram.types import BufferedInputFile, InputMediaPhoto, Message

from .config import Settings
from .data_fetcher import DaySummary, IMOEXFetcher, MOSCOW_TZ
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
        self._background_tasks: set[asyncio.Task[None]] = set()
        self._alert_history: List[Tuple[datetime, float]] = []
        self._last_price_text: Dict[int, str] = {}

    def register_handlers(self, dispatcher: Dispatcher) -> None:
        dispatcher.message.register(self._handle_start, CommandStart())

    async def on_startup(self, bot: Bot) -> None:
        await self._prepare_history()

        self._start_background_task(
            self._background_price_loop(bot), "price-updater"
        )
        self._start_background_task(
            self._background_chart_loop(bot), "chart-updater"
        )

    async def on_shutdown(self, bot: Bot) -> None:  # pragma: no cover
        self.storage.save()
        tasks = list(self._background_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._background_tasks.clear()

    async def _handle_start(self, message: Message, bot: Bot) -> None:
        chat_id = message.chat.id

        async with self._lock:
            self.storage.state.ensure_chat(chat_id)
            await self._ensure_chat_messages(chat_id, bot)
            last_point = self.storage.state.last_point()

        await message.answer(
            "Привет! Я буду присылать обновления индекса и свежий график в этом чате."
        )

        if last_point is not None:
            timestamp, value = last_point
            await self._update_price_messages(
                self._format_price_message(timestamp, value),
                bot,
                chat_ids=[chat_id],
            )
            await self._update_chart_messages(bot, chat_ids=[chat_id])

    async def _prepare_history(self) -> None:
        state = self.storage.state
        state.prune_history(timedelta(hours=6))
        if state.history:
            logger.info("Loaded %d history points from state", len(state.history))
            return

        end_time = datetime.now(MOSCOW_TZ)
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

    async def _ensure_chat_messages(self, chat_id: int, bot: Bot) -> None:
        chat_state = self.storage.state.ensure_chat(chat_id)
        placeholder_text = "♻️ Перезапуск отслеживания…"

        price_message_id = chat_state.price_message_id
        need_new_price_message = price_message_id is None
        if price_message_id is not None:
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=price_message_id,
                    text=placeholder_text,
                )
                self._last_price_text[chat_id] = placeholder_text
            except TelegramAPIError:
                logger.warning(
                    "Stored price message for chat %s is not accessible, creating new one",
                    chat_id,
                )
                need_new_price_message = True

        if need_new_price_message:
            try:
                message = await bot.send_message(
                    chat_id=chat_id, text=placeholder_text
                )
            except TelegramAPIError:
                logger.exception(
                    "Failed to create price placeholder message for chat %s", chat_id
                )
            else:
                chat_state.price_message_id = message.message_id
                self._last_price_text[chat_id] = placeholder_text

        placeholder_chart = await self._build_placeholder_chart()
        chart_bytes = placeholder_chart.getvalue()
        chart_caption = "Обновляю график…"

        chart_message_id = chat_state.chart_message_id
        need_new_chart_message = chart_message_id is None
        if chart_message_id is not None:
            try:
                await bot.edit_message_media(
                    chat_id=chat_id,
                    message_id=chart_message_id,
                    media=InputMediaPhoto(
                        media=self._buffer_to_input_file(chart_bytes),
                        caption=chart_caption,
                    ),
                )
            except TelegramAPIError:
                logger.warning(
                    "Stored chart message for chat %s is not accessible, creating new one",
                    chat_id,
                )
                need_new_chart_message = True

        if need_new_chart_message:
            try:
                message = await bot.send_photo(
                    chat_id=chat_id,
                    photo=self._buffer_to_input_file(chart_bytes),
                    caption=chart_caption,
                )
            except TelegramAPIError:
                logger.exception(
                    "Failed to create chart placeholder for chat %s", chat_id
                )
            else:
                chat_state.chart_message_id = message.message_id

        self.storage.save()

    def _start_background_task(self, coro: Coroutine[Any, Any, None], name: str) -> None:
        task = asyncio.create_task(coro, name=name)
        self._background_tasks.add(task)

        def _log_task_result(fut: asyncio.Task[None]) -> None:
            self._background_tasks.discard(fut)
            try:
                fut.result()
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("Background task %s failed", name)

        task.add_done_callback(_log_task_result)

    async def _background_price_loop(self, bot: Bot) -> None:
        while True:
            await self._perform_price_update(bot)
            await asyncio.sleep(self.settings.price_update_interval)

    async def _background_chart_loop(self, bot: Bot) -> None:
        await asyncio.sleep(self.settings.chart_update_interval)
        while True:
            await self._perform_chart_update(bot)
            await asyncio.sleep(self.settings.chart_update_interval)

    async def _perform_price_update(self, bot: Bot) -> None:
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

            await self._update_price_messages(
                self._format_price_message(timestamp, value), bot
            )
            await self._handle_alert(timestamp, value, bot)

    async def _perform_chart_update(self, bot: Bot) -> None:
        async with self._lock:
            try:
                await self._update_chart_messages(bot)
            except Exception:
                logger.exception("Failed to update chart messages")

    async def _update_price_messages(
        self, text: str, bot: Bot, *, chat_ids: Iterable[int] | None = None
    ) -> None:
        state = self.storage.state
        targets = (
            ((chat_id, state.ensure_chat(chat_id)) for chat_id in chat_ids)
            if chat_ids is not None
            else state.iter_chats()
        )

        updated = False
        for chat_id, chat_state in targets:
            message_id = chat_state.price_message_id
            if message_id is None:
                try:
                    message = await bot.send_message(chat_id=chat_id, text=text)
                except TelegramAPIError:
                    logger.exception(
                        "Failed to send price message to chat %s", chat_id
                    )
                    continue
                chat_state.price_message_id = message.message_id
                self._last_price_text[chat_id] = text
                updated = True
                continue

            if self._last_price_text.get(chat_id) == text:
                continue

            try:
                await bot.edit_message_text(
                    chat_id=chat_id, message_id=message_id, text=text
                )
            except TelegramAPIError:
                logger.exception("Failed to edit price message for chat %s", chat_id)
                try:
                    message = await bot.send_message(chat_id=chat_id, text=text)
                except TelegramAPIError:
                    logger.exception(
                        "Failed to resend price message to chat %s", chat_id
                    )
                    continue
                chat_state.price_message_id = message.message_id
            self._last_price_text[chat_id] = text
            updated = True

        if updated:
            self.storage.save()

    async def _update_chart_messages(
        self, bot: Bot, *, chat_ids: Iterable[int] | None = None
    ) -> None:
        state = self.storage.state
        points = list(state.iter_points())
        if not points:
            return

        cutoff = datetime.now(MOSCOW_TZ) - timedelta(hours=5)
        recent_points = [(ts, value) for ts, value in points if ts >= cutoff]
        if not recent_points:
            recent_points = points[-2:]

        summary: DaySummary | None = None
        try:
            summary = await asyncio.to_thread(self.fetcher.fetch_day_summary)
        except Exception:
            logger.warning("Failed to fetch day summary for chart", exc_info=True)

        chart_buffer = await asyncio.to_thread(build_chart, recent_points, summary)
        chart_bytes = chart_buffer.getvalue()
        caption = self._build_chart_caption(recent_points)

        targets = (
            ((chat_id, state.ensure_chat(chat_id)) for chat_id in chat_ids)
            if chat_ids is not None
            else state.iter_chats()
        )

        updated = False
        for chat_id, chat_state in targets:
            message_id = chat_state.chart_message_id
            media = InputMediaPhoto(
                media=self._buffer_to_input_file(chart_bytes), caption=caption
            )

            if message_id is None:
                try:
                    message = await bot.send_photo(
                        chat_id=chat_id,
                        photo=self._buffer_to_input_file(chart_bytes),
                        caption=caption,
                    )
                except TelegramAPIError:
                    logger.exception(
                        "Failed to send chart message to chat %s", chat_id
                    )
                    continue
                chat_state.chart_message_id = message.message_id
                updated = True
                continue

            try:
                await bot.edit_message_media(
                    chat_id=chat_id, message_id=message_id, media=media
                )
            except TelegramAPIError:
                logger.exception("Failed to edit chart message for chat %s", chat_id)
                try:
                    message = await bot.send_photo(
                        chat_id=chat_id,
                        photo=self._buffer_to_input_file(chart_bytes),
                        caption=caption,
                    )
                except TelegramAPIError:
                    logger.exception(
                        "Failed to resend chart message to chat %s", chat_id
                    )
                    continue
                chat_state.chart_message_id = message.message_id
            updated = True

        if updated:
            self.storage.save()

    @staticmethod
    def _buffer_to_input_file(data: bytes) -> BufferedInputFile:
        return BufferedInputFile(data, filename="imoex_chart.png")

    async def _build_placeholder_chart(self) -> BytesIO:
        points = list(self.storage.state.iter_points())
        if not points:
            now = datetime.now(MOSCOW_TZ)
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
        self, timestamp: datetime, value: float, bot: Bot
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
            text = (
                f"{arrow} {direction} индекса на {diff:+.2f} пунктов за минуту!\n"
                f"Текущее значение: {value:.2f}"
            )

            for chat_id, chat_state in list(self.storage.state.iter_chats()):
                try:
                    message = await bot.send_message(chat_id=chat_id, text=text)
                except TelegramAPIError:
                    logger.exception(
                        "Failed to send alert message to chat %s", chat_id
                    )
                    continue
                self._start_background_task(
                    self._delete_message_later(bot, chat_id, message.message_id),
                    f"delete-alert-{chat_id}-{message.message_id}",
                )
            self._alert_history.clear()

    async def _delete_message_later(
        self, bot: Bot, chat_id: int, message_id: int
    ) -> None:
        await asyncio.sleep(3600)
        try:
            await bot.delete_message(chat_id=chat_id, message_id=message_id)
        except TelegramAPIError:
            logger.warning(
                "Failed to delete alert message %s in chat %s", message_id, chat_id
            )


__all__ = ["IMOEXBotService"]
