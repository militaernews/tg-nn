import asyncio
import logging
import time
from datetime import datetime
from typing import Optional
import os
from collections import defaultdict
from pathlib import Path

from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import Message, InputMediaVideo, InputMediaPhoto

from bot.config import CHANNEL_BACKUP, PASSWORD, CONTAINER, GROUP_LOG, CHANNEL_UA, GIVEAWAY_DESTINATION
from bot.db import get_accounts, get_post, set_post, DBPool
from bot.db_cache import get_cache
from bot.destination import get_destination
from bot.error_logger import log_error
from bot.model import Post
from bot.translation import debloat_text, format_text, translate
from bot.extension.militarnyi import get_militarnyi
from bot.extension.postillon import get_postillon

# MediaGroup buffering
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
        log_filename = f"logs/processor_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        logging.basicConfig(format=format_str, level=level, datefmt=date_fmt,
                            filename=log_filename, force=True)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("pyrogram").setLevel(logging.WARNING)


async def handle_extensions(client: Client, message: Message, cache) -> bool:
    """Handle special sources like Militarnyi or Postillon."""
    source_chat_id = message.forward_from_chat.id if message.forward_from_chat else message.chat.id

    if source_chat_id == -1001011817559:
        logging.info(f"Processing Militarnyi extension for {message.id}")
        cp = await get_militarnyi(message)
        source = await cache.get_source(source_chat_id)

        medias = [InputMediaVideo(v) for v in cp.video_urls] + \
                 [InputMediaPhoto(v) for v in cp.image_urls]

        if medias:
            caption = await format_text(translate(cp.caption), message, source, message.id, cache)
            medias[0].caption = caption
            msgs = await client.send_media_group(CHANNEL_UA, medias)
            msg = msgs[0]

            for text in cp.texts:
                text = await format_text(translate(text), message, source, message.id, cache)
                await client.send_message(CHANNEL_UA, text,
                                          reply_to_message_id=msg.id,
                                          disable_web_page_preview=True)

            for f in cp.image_urls + cp.video_urls:
                Path(f).unlink(missing_ok=True)
            return True

    if source_chat_id == -1001123527809:
        logging.info(f"Processing Postillon extension for {message.id}")
        cp = await get_postillon(message)
        source = await cache.get_source(source_chat_id)

        if cp.image_urls:
            caption = await format_text(cp.caption, message, source, message.id, cache)
            await client.send_photo(source.destination, cp.image_urls[0], caption=caption)
            return True

    return False


async def handle_giveaway_logic(client: Client, message: Message, cache) -> bool:
    """Handle giveaway forwarding from backup to GIVEAWAY_DESTINATION."""
    if not GIVEAWAY_DESTINATION:
        return False
    if not (message.giveaway or message.giveaway_completed or message.giveaway_winners):
        return False
    if not message.forward_from_chat:
        return False

    source_chat_id = message.forward_from_chat.id
    source_msg_id = message.forward_from_message_id

    existing_post = await get_post(source_chat_id, source_msg_id)
    if existing_post and existing_post.destination == GIVEAWAY_DESTINATION:
        return True

    source = await cache.get_source(source_chat_id)
    if not source or not source.is_active:
        return False

    try:
        await message.forward(GIVEAWAY_DESTINATION)

        detail_text = f"Giveaway von: <a href='{message.link}'>{source.display_name}"
        if source.bias:
            detail_text += f" {source.bias}"
        detail_text += "</a>"
        if source.username is None and source.invite is not None:
            detail_text += f" | <a href='https://t.me/+{source.invite}'>🔗</a>"
        if source.detail_id is not None:
            detail_text += f" | <a href='https://t.me/nn_sources/{source.detail_id}'>ℹ️</a>"

        new_msg = await client.send_message(GIVEAWAY_DESTINATION, detail_text,
                                            disable_web_page_preview=True)
        await set_post(Post(
            destination=GIVEAWAY_DESTINATION,
            message_id=new_msg.id,
            source_channel_id=source_chat_id,
            source_message_id=source_msg_id,
            backup_id=message.id,
            message_text=detail_text,
        ))
        logging.info(f"Forwarded giveaway from {source_chat_id} to {GIVEAWAY_DESTINATION}")
        return True
    except Exception as e:
        logging.error(f"Failed to forward giveaway: {e}")
        await log_error(client, e, f"[processor] Failed to forward giveaway {source_chat_id}/{source_msg_id}")
        return False


async def process_message_logic(client: Client, message: Message, cache,
                                is_media_group: bool = False) -> None:
    start_time = time.perf_counter()

    # 0. Giveaways
    if message.giveaway or message.giveaway_completed or message.giveaway_winners:
        if await handle_giveaway_logic(client, message, cache):
            return

    # 1. Must be a forward
    if not message.forward_from_chat:
        logging.warning(f"Message {message.id} is not a forward, skipping")
        return

    source_chat_id = message.forward_from_chat.id
    source_msg_id = message.forward_from_message_id

    # 2. Dedup — cheap memory check before any DB call
    cache_obj = cache  # cache is already a DBCache instance
    if hasattr(cache_obj, 'is_duplicate_message') and \
            cache_obj.is_duplicate_message(source_chat_id, source_msg_id):
        logging.info(f"Duplicate message {source_chat_id}/{source_msg_id}, skipping")
        return

    # 3. Already posted? (DB call — connection acquired + released immediately)
    existing_post = await get_post(source_chat_id, source_msg_id)
    if existing_post and existing_post.destination != CHANNEL_BACKUP:
        logging.info(f"{source_chat_id}/{source_msg_id} already posted, skipping")
        return

    # 4. Source check (cache — no DB call)
    source = await cache.get_source(source_chat_id)
    if not source or not source.is_spread:
        logging.info(f"Source {source_chat_id} not marked for spreading, skipping")
        return

    # 5. Extensions
    if await handle_extensions(client, message, cache):
        return

    # 6. Debloat
    text = await debloat_text(message, client, cache,
                               is_caption=bool(message.caption or message.text))
    if not text:
        logging.info(f"{source_chat_id}/{source_msg_id} empty after debloat, skipping")
        return

    # 7. Route (LLM call — no DB)
    destination = await get_destination(text, source_chat_id, cache)
    if not destination:
        logging.warning(f"No destination for {source_chat_id}/{source_msg_id}")
        return

    # 8. Format (cache — no DB)
    footer = await cache.get_footer(destination)
    formatted_text = await format_text(text, message, source, message.id, footer)

    # 9. Reply threading (DB call — connection acquired + released immediately)
    reply_to_id = None
    if message.reply_to_message_id:
        reply_post = await get_post(CHANNEL_BACKUP, message.reply_to_message_id)
        if reply_post:
            dest_post = await get_post(reply_post.source_channel_id, reply_post.source_message_id)
            if dest_post and dest_post.destination == destination:
                reply_to_id = dest_post.message_id

    # 10. Post
    try:
        if is_media_group:
            msgs = await client.copy_media_group(
                destination,
                from_chat_id=CHANNEL_BACKUP,
                message_id=message.id,
                captions=formatted_text,
                reply_to_message_id=reply_to_id,
            )
            new_msg = msgs[0]
        elif message.text:
            new_msg = await client.send_message(
                destination, formatted_text,
                reply_to_message_id=reply_to_id,
                disable_web_page_preview=True,
            )
        else:
            new_msg = await message.copy(
                destination,
                caption=formatted_text,
                reply_to_message_id=reply_to_id,
            )

        # 11. Record (DB call — connection acquired + released immediately)
        await set_post(Post(
            destination=new_msg.chat.id,
            message_id=new_msg.id,
            source_channel_id=source_chat_id,
            source_message_id=source_msg_id,
            backup_id=message.id,
            reply_id=reply_to_id,
            message_text=formatted_text,
        ))

        elapsed = (time.perf_counter() - start_time) * 1000
        logging.info(f"Posted {source_chat_id}/{source_msg_id} → {destination} in {elapsed:.1f}ms")

    except Exception as e:
        logging.error(f"Failed to post to {destination}: {e}")
        await log_error(client, e, f"[processor] Failed to post {source_chat_id}/{source_msg_id} to {destination}")


async def handle_backup_message(client: Client, message: Message, cache) -> None:
    if message.media_group_id:
        mg_id = message.media_group_id
        async with media_group_locks[mg_id]:
            media_groups[mg_id].append(message)
            if len(media_groups[mg_id]) == 1:
                await asyncio.sleep(2)
                group = sorted(media_groups[mg_id], key=lambda m: m.id)
                await process_message_logic(client, group[0], cache, is_media_group=True)
                del media_groups[mg_id]
                # Clean up the lock too
                if mg_id in media_group_locks:
                    del media_group_locks[mg_id]
    else:
        await process_message_logic(client, message, cache)


async def main():
    add_logging()
    cache = get_cache()

    # Warm cache (opens DB, fetches, closes connection immediately after)
    await cache.warm_cache()

    # Close the pool after warmup so Neon can scale to zero between messages
    await DBPool.close_pool()

    accounts = await get_accounts()
    if not accounts:
        logging.error("No accounts found")
        return

    a = accounts[0]
    logging.info(f"Starting Processor: {a.name} monitoring {CHANNEL_BACKUP}")

    app = Client(
        name=f"processor_{a.name}",
        api_id=a.api_id,
        api_hash=a.api_hash,
        phone_number=a.phone_number,
        password=PASSWORD,
        parse_mode=ParseMode.HTML,
    )

    @app.on_message(filters.chat(CHANNEL_BACKUP) & filters.incoming)
    async def on_backup_msg(client: Client, message: Message):
        try:
            await handle_backup_message(client, message, cache)
        except Exception as e:
            # Pyrogram has no application-wide error hook (unlike PTB's
            # add_error_handler) - catch here so nothing goes unreported.
            logging.error(f"Unhandled error in on_backup_msg for message {message.id}: {e}")
            await log_error(client, e, f"[processor] on_backup_msg {message.id}")

    await app.start()
    logging.info("Processor started, idling...")

    try:
        await asyncio.Event().wait()
    finally:
        # Graceful shutdown: close pool so Neon suspends promptly
        await DBPool.close_pool()
        await app.stop()


if __name__ == "__main__":
    asyncio.run(main())