"""
Error Logger for tg-nn: posts errors to the shared ptb-* log group topic.

Unlike the ptb-* bots (python-telegram-bot, each with its own bot token and a
single application-wide add_error_handler), tg-nn runs as Pyrogram/Kurigram
user accounts with no bot token of its own and no equivalent global error
hook - so error reporting piggybacks on whichever account Client is already
running (collector or processor), and call sites report explicitly.
"""

import logging
import traceback
from typing import Optional

from pyrogram import Client
from pyrogram.errors import RPCError

from bot.config import LOG_GROUP_ID, THREAD_ID

logger = logging.getLogger(__name__)


async def log_error(client: Client, error: Exception, context_msg: Optional[str] = None) -> None:
    """Log an error to both the console and the shared LOG_GROUP_ID/THREAD_ID
    Telegram topic, using the given (already-running) account Client.
    """
    error_trace = traceback.format_exc()
    logger.error(f"[ERROR] {context_msg or 'Unhandled exception'}\n{error_trace}")

    try:
        await client.send_message(
            LOG_GROUP_ID,
            _format_error_message(error, context_msg, error_trace),
            message_thread_id=THREAD_ID,
        )
    except RPCError as tg_error:
        logger.error(f"Failed to post error to Telegram group topic: {tg_error}")
    except Exception as e:
        logger.error(f"Unexpected error while logging to Telegram: {e}")


def _format_error_message(error: Exception, context_msg: Optional[str], trace: str) -> str:
    msg = "🚨 <b>Bot Error (tg-nn)</b>\n\n"
    if context_msg:
        msg += f"<b>Context:</b> {context_msg}\n\n"
    msg += f"<b>Error Type:</b> <code>{type(error).__name__}</code>\n"
    msg += f"<b>Message:</b> <code>{str(error)}</code>\n\n"
    trace_lines = trace.split('\n')[-5:]  # Last 5 lines
    trace_text = '\n'.join(trace_lines)
    msg += f"<b>Traceback (last lines):</b>\n<pre>{trace_text}</pre>"
    return msg
