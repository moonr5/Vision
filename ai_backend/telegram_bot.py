import io
import logging
import os
import re
from typing import Optional

from telegram import Update, Bot
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

logger = logging.getLogger(__name__)

# Keywords that trigger a PDF report generation
_REPORT_KEYWORDS = re.compile(
    r"\b(report|pdf|summary|export|generate|fleet report|send report)\b",
    re.IGNORECASE,
)


class TelegramBot:
    def __init__(self, token: str, analyzer, chat_id: Optional[str] = None):
        self._token = token
        self._analyzer = analyzer
        self._chat_id = chat_id  # optional: restrict to one chat
        self._app: Optional[Application] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self):
        self._app = Application.builder().token(self._token).build()
        self._app.add_handler(CommandHandler("start", self._cmd_start))
        self._app.add_handler(CommandHandler("help", self._cmd_help))
        self._app.add_handler(CommandHandler("score", self._cmd_score))
        self._app.add_handler(CommandHandler("events", self._cmd_events))
        self._app.add_handler(CommandHandler("metrics", self._cmd_metrics))
        self._app.add_handler(CommandHandler("report", self._cmd_report))
        self._app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_text)
        )
        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling(drop_pending_updates=True)
        logger.info("[Telegram] Bot polling started")

    async def stop(self):
        if self._app:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
            logger.info("[Telegram] Bot stopped")

    # ------------------------------------------------------------------
    # Auth guard
    # ------------------------------------------------------------------

    def _allowed(self, update: Update) -> bool:
        if not self._chat_id:
            return True
        return str(update.effective_chat.id) == str(self._chat_id)

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    async def _cmd_start(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not self._allowed(update):
            return
        await update.message.reply_text(
            "SGU Logistics Fleet AI\n\n"
            "Commands:\n"
            "/report — generate PDF fleet report\n"
            "/score — driver safety scores\n"
            "/events — recent critical events\n"
            "/metrics — fleet KPIs\n"
            "/help — show this message\n\n"
            "Or just ask me anything about the fleet."
        )

    async def _cmd_help(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        await self._cmd_start(update, ctx)

    async def _cmd_score(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not self._allowed(update):
            return
        await self._ai_reply(update, "Show me the driver safety scores ranked from best to worst.")

    async def _cmd_events(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not self._allowed(update):
            return
        await self._ai_reply(update, "What are the most critical recent fleet events?")

    async def _cmd_metrics(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not self._allowed(update):
            return
        await self._ai_reply(update, "Give me the key fleet metrics and overall status.")

    async def _cmd_report(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not self._allowed(update):
            return
        await self._send_report(update)

    # ------------------------------------------------------------------
    # Free-text handler
    # ------------------------------------------------------------------

    async def _handle_text(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not self._allowed(update):
            return
        text = update.message.text or ""
        if _REPORT_KEYWORDS.search(text):
            await self._send_report(update)
        else:
            await self._ai_reply(update, text)

    # ------------------------------------------------------------------
    # Core helpers
    # ------------------------------------------------------------------

    async def _ai_reply(self, update: Update, question: str):
        from db import get_fleet_snapshot

        try:
            context = await get_fleet_snapshot()
        except Exception:
            context = {}

        try:
            answer = self._analyzer.analyze(question, context) if self._analyzer else (
                "AI backend is not configured."
            )
        except Exception as e:
            answer = f"Sorry, I couldn't process that request. ({e})"

        await update.message.reply_text(answer)

    async def _send_report(self, update: Update):
        from db import get_report_data, get_fleet_snapshot
        from report_generator import generate_pdf

        msg = await update.message.reply_text("Generating fleet report, please wait...")

        try:
            report_data = await get_report_data()
            fleet_snapshot = await get_fleet_snapshot()

            # Get AI summary for the report
            ai_summary = ""
            if self._analyzer:
                try:
                    ai_summary = self._analyzer.analyze(
                        "Summarize the current fleet status and the top 2 issues requiring immediate attention.",
                        fleet_snapshot,
                    )
                except Exception:
                    pass

            pdf_bytes = generate_pdf(report_data, ai_summary=ai_summary)

            from datetime import datetime
            filename = f"fleet_report_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf"

            await update.message.reply_document(
                document=io.BytesIO(pdf_bytes),
                filename=filename,
                caption="SGU Logistics Fleet Report — generated by Claude AI",
            )
            await msg.delete()

        except Exception as e:
            logger.exception("Report generation failed")
            await msg.edit_text(f"Failed to generate report: {e}")


# ------------------------------------------------------------------
# Module-level singleton
# ------------------------------------------------------------------

_bot: Optional[TelegramBot] = None


def get_bot() -> Optional[TelegramBot]:
    return _bot


async def init_bot(token: str, analyzer, chat_id: Optional[str] = None):
    global _bot
    _bot = TelegramBot(token, analyzer, chat_id)
    await _bot.start()


async def shutdown_bot():
    global _bot
    if _bot:
        await _bot.stop()
        _bot = None
