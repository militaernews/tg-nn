import asyncio
import logging
from datetime import datetime
from typing import Optional
import os

from pyrogram import Client, filters, compose
from pyrogram.enums import ParseMode
from pyrogram.types import Message

from bot.config import CHANNEL_BACKUP, PASSWORD, CONTAINER
from bot.db import get_accounts, get_source_ids_by_api_id, set_post, get_post, DBPool
from bot.db_cache import get_cache
from bot.error_logger import log_error
from bot.model import Post


def add_logging():
    level = logging.INFO
    format_str = "%(asctime)s %(levelname)-5s %(funcName)-20s [%(filename)s:%(lineno)d]: %(message)s"
    date_fmt = '%Y-%m-%d %H:%M:%S'

    if CONTAINER:
        logging.basicConfig(format=format_str, level=level, datefmt=date_fmt,
                            handlers=[logging.StreamHandler()], force=True)
    else:
        os.makedirs("logs", exist_ok=True)
        log_filename = f"logs/collector_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        logging.basicConfig(format=format_str, level=level, datefmt=date_fmt,
                            filename=log_filename, force=True)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("pyrogram").setLevel(logging.WARNING)


async def backup_message(client: Client, message: Message) -> Optional[int]:
    """Forward message to backup channel and return backup message ID."""
    try:
        msg_backup = await client.forward_messages(CHANNEL_BACKUP, message.chat.id, message.id)
        backup_id = msg_backup[0].id if isinstance(msg_backup, list) else msg_backup.id
        logging.info(f"Forwarded {message.chat.id}/{message.id} → backup/{backup_id}")
        return backup_id
    except Exception as e:
        logging.error(f"Failed to forward {message.chat.id}/{message.id}: {e}")
        await log_error(client, e, f"[collector] Failed to forward {message.chat.id}/{message.id}")
        return None


async def main():
    add_logging()
    cache = get_cache()

    # Warm cache then release DB connection so Neon can scale to zero
    await cache.warm_cache()
    await DBPool.close_pool()

    accounts = await get_accounts()
    apps = []

    for a in accounts:
        logging.info(f"Starting Collector: {a.name}")
        app = Client(
            name=f"collector_{a.name}",
            api_id=a.api_id,
            api_hash=a.api_hash,
            phone_number=a.phone_number,
            password=PASSWORD,
            parse_mode=ParseMode.HTML,
        )

        sources = await get_source_ids_by_api_id(a.api_id)
        if not sources:
            logging.warning(f"No active sources for {a.name}")
            continue

        logging.info(f"{a.name} monitoring {len(sources)} sources")

        source_filter = filters.chat(sources) & ~filters.forwarded & filters.incoming

        @app.on_message(source_filter)
        async def handle_incoming(client: Client, message: Message):
            try:
                # Cache check — no DB
                source = await cache.get_source(message.chat.id)
                if not source or not source.is_active:
                    return

                # Forward to backup (Telegram API — no DB)
                backup_id = await backup_message(client, message)
                if not backup_id:
                    return

                # Reply threading (DB call — connection opens, query runs, closes)
                reply_id = None
                if message.reply_to_message_id:
                    reply_post = await get_post(message.chat.id, message.reply_to_message_id)
                    if reply_post:
                        reply_id = reply_post.backup_id

                # Record (DB call — connection opens, insert runs, closes)
                # destination=None marks this as backed up but not yet routed
                # to a real destination; the processor fills that in later.
                await set_post(Post(
                    destination=None,
                    message_id=backup_id,
                    source_channel_id=message.chat.id,
                    source_message_id=message.id,
                    backup_id=backup_id,
                    reply_id=reply_id,
                    message_text=message.text or message.caption,
                ))
            except Exception as e:
                # Pyrogram has no application-wide error hook (unlike PTB's
                # add_error_handler) - catch here so nothing goes unreported.
                logging.error(f"Unhandled error in handle_incoming for {message.chat.id}/{message.id}: {e}")
                await log_error(client, e, f"[collector] handle_incoming {message.chat.id}/{message.id}")

        apps.append(app)

    if not apps:
        logging.error("No accounts to start")
        return

    try:
        await compose(apps)
    finally:
        await DBPool.close_pool()


if __name__ == "__main__":
    asyncio.run(main())