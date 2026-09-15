import asyncio
import logging
from collections import defaultdict
from datetime import datetime
from typing import List, Optional
import os

from pyrogram import Client, filters, compose
from pyrogram.enums import ParseMode
from pyrogram.types import Message

from bot.config import CHANNEL_BACKUP, PASSWORD, CONTAINER
from bot.db import get_accounts, get_source_ids_by_api_id, set_post, get_post, DBPool
from bot.db_cache import get_cache
from bot.error_logger import log_error
from bot.model import Post

# MediaGroup buffering - collect every part of an album before forwarding it
# to the backup channel in a single call, so Telegram keeps the parts grouped
# there instead of splitting the album into separate single-message forwards.
media_groups: dict = defaultdict(list)
media_group_locks: dict = defaultdict(asyncio.Lock)


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


async def backup_media_group(client: Client, chat_id: int, messages: List[Message]) -> Optional[List[Message]]:
    """Forward a whole album to the backup channel in a single call, so
    Telegram keeps the parts grouped there instead of turning each part into
    its own separate post."""
    msg_ids = [m.id for m in messages]
    try:
        msgs_backup = await client.forward_messages(CHANNEL_BACKUP, chat_id, msg_ids)
        if not isinstance(msgs_backup, list):
            msgs_backup = [msgs_backup]
        logging.info(f"Forwarded media group {chat_id}/{msg_ids} → backup/{[m.id for m in msgs_backup]}")
        return msgs_backup
    except Exception as e:
        logging.error(f"Failed to forward media group {chat_id}/{msg_ids}: {e}")
        await log_error(client, e, f"[collector] Failed to forward media group {chat_id}/{msg_ids}")
        return None


async def record_post(message: Message, backup_id: int) -> None:
    reply_id = None
    if message.reply_to_message_id:
        reply_post = await get_post(message.chat.id, message.reply_to_message_id)
        if reply_post:
            reply_id = reply_post.backup_id

    # destination=None marks this as backed up but not yet routed to a real
    # destination; the processor fills that in later.
    await set_post(Post(
        destination=None,
        message_id=backup_id,
        source_channel_id=message.chat.id,
        source_message_id=message.id,
        backup_id=backup_id,
        reply_id=reply_id,
        message_text=message.text or message.caption,
    ))


async def process_media_group(client: Client, cache, chat_id: int, mg_id: str) -> None:
    async with media_group_locks[mg_id]:
        group = sorted(media_groups.pop(mg_id, []), key=lambda m: m.id)
        media_group_locks.pop(mg_id, None)

    if not group:
        return

    source = await cache.get_source(chat_id)
    if not source or not source.is_active:
        return

    backup_msgs = await backup_media_group(client, chat_id, group)
    if not backup_msgs:
        return

    for orig, backup_msg in zip(group, backup_msgs):
        await record_post(orig, backup_msg.id)


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
                if message.media_group_id:
                    mg_id = message.media_group_id
                    # Only append while holding the lock, then release it
                    # immediately - holding it across the sleep+forward below
                    # would block every other part of the same album from
                    # appending until the first part had already been
                    # forwarded alone, splitting the album into single posts.
                    async with media_group_locks[mg_id]:
                        media_groups[mg_id].append(message)
                        is_first = len(media_groups[mg_id]) == 1

                    if is_first:
                        await asyncio.sleep(2)
                        await process_media_group(client, cache, message.chat.id, mg_id)
                    return

                # Cache check — no DB
                source = await cache.get_source(message.chat.id)
                if not source or not source.is_active:
                    return

                # Forward to backup (Telegram API — no DB)
                backup_id = await backup_message(client, message)
                if not backup_id:
                    return

                # Record (DB call — connection opens, insert runs, closes)
                await record_post(message, backup_id)
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