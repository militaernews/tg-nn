import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import List, Final, Optional, Dict
import os
from collections import defaultdict
from pathlib import Path

from pyrogram import Client, filters, compose
from pyrogram.enums import ParseMode
from pyrogram.errors import MessageNotModified
from pyrogram.types import Message, InputMediaVideo, InputMediaPhoto

from bot.config import CHANNEL_BACKUP, PASSWORD, CONTAINER, GROUP_LOG, CHANNEL_UA
from bot.db import get_accounts, get_post, set_post
from bot.db_cache import get_cache
from bot.destination import get_destination
from bot.model import Post
from bot.translation import debloat_text, format_text, translate
from bot.extension.militarnyi import get_militarnyi
from bot.extension.postillon import get_postillon

# MediaGroup buffering
media_groups = defaultdict(list)
media_group_locks = defaultdict(asyncio.Lock)

def add_logging():
    level = logging.INFO
    format_str = "%(asctime)s %(levelname)-5s %(funcName)-20s [%(filename)s:%(lineno)d]: %(message)s"
    date_fmt = '%Y-%m-%d %H:%M:%S'
    
    if CONTAINER:
        logging.basicConfig(format=format_str, level=level, datefmt=date_fmt, handlers=[logging.StreamHandler()], force=True)
    else:
        log_filename = f"logs/processor_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        os.makedirs("logs", exist_ok=True)
        logging.basicConfig(format=format_str, level=level, datefmt=date_fmt, filename=log_filename, force=True)
    
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("pyrogram").setLevel(logging.WARNING)

async def handle_extensions(client: Client, message: Message, cache):
    """Handle special sources like Militarnyi or Postillon."""
    source_chat_id = message.forward_from_chat.id if message.forward_from_chat else message.chat.id
    
    # Militarnyi Extension (Example logic from legacy main.py)
    if source_chat_id == -1001011817559: # Example ID for Militarnyi
        logging.info(f"Processing Militarnyi extension for {message.id}")
        cp = await get_militarnyi(message)
        source = await cache.get_source(source_chat_id)
        
        medias = []
        for v in cp.video_urls:
            medias.append(InputMediaVideo(v))
        for v in cp.image_urls:
            medias.append(InputMediaPhoto(v))
        
        if medias:
            caption = await format_text(translate(cp.caption), message, source, message.id, cache)
            medias[0].caption = caption
            msgs = await client.send_media_group(CHANNEL_UA, medias)
            msg = msgs[0]
            
            for text in cp.texts:
                text = await format_text(translate(text), message, source, message.id, cache)
                await client.send_message(CHANNEL_UA, text, reply_to_message_id=msg.id, disable_web_page_preview=True)
            
            for f in cp.image_urls + cp.video_urls:
                Path(f).unlink(missing_ok=True)
            return True

    # Postillon Extension
    if source_chat_id == -1001123527809: # Example ID for Postillon
        logging.info(f"Processing Postillon extension for {message.id}")
        cp = await get_postillon(message)
        source = await cache.get_source(source_chat_id)
        
        if cp.image_urls:
            caption = await format_text(cp.caption, message, source, message.id, cache)
            await client.send_photo(source.destination, cp.image_urls[0], caption=caption)
            return True
            
    return False

async def process_message_logic(client: Client, message: Message, cache, is_media_group=False):
    """Core processing logic for a single message or the first message of a media group."""
    start_time = time.perf_counter()
    
    # 1. Identify original source from forward info
    if not message.forward_from_chat:
        logging.warning(f"Message {message.id} in backup is not a forward, skipping")
        return None
        
    source_chat_id = message.forward_from_chat.id
    source_msg_id = message.forward_from_message_id
    
    # 2. Check if already posted (Database lookup)
    existing_post = await get_post(source_chat_id, source_msg_id)
    if existing_post and existing_post.destination != CHANNEL_BACKUP:
        logging.info(f"Message {source_chat_id}/{source_msg_id} already posted to {existing_post.destination}, skipping")
        return None
        
    # 3. Check if source should be spread
    source = await cache.get_source(source_chat_id)
    if not source or not source.is_spread:
        logging.info(f"Source {source_chat_id} is not marked for spreading, skipping")
        return None

    # 4. Handle Extensions
    if await handle_extensions(client, message, cache):
        return None
        
    # 5. Debloat and prepare text
    text = await debloat_text(message, client, cache, is_caption=bool(message.caption or message.text))
    if not text:
        logging.info(f"Message {source_chat_id}/{source_msg_id} has no text after debloating, skipping")
        return None
        
    # 6. Content-based routing (LLM)
    destination = await get_destination(text, source_chat_id, cache)
    if not destination:
        logging.warning(f"No destination determined for message {source_chat_id}/{source_msg_id}")
        return None
        
    # 7. Format with footer
    footer = await cache.get_footer(destination)
    formatted_text = await format_text(text, message, source, message.id, footer)
    
    # 8. Handle Replies
    reply_to_id = None
    if message.reply_to_message_id:
        # Find the post in the destination channel that corresponds to the replied-to message in backup
        reply_post = await get_post(CHANNEL_BACKUP, message.reply_to_message_id)
        if reply_post:
            dest_post = await get_post(reply_post.source_channel_id, reply_post.source_message_id)
            if dest_post and dest_post.destination == destination:
                reply_to_id = dest_post.message_id

    # 9. Post to destination
    try:
        if is_media_group:
            msgs = await client.copy_media_group(
                destination,
                from_chat_id=CHANNEL_BACKUP,
                message_id=message.id,
                captions=formatted_text,
                reply_to_message_id=reply_to_id
            )
            new_msg = msgs[0]
        else:
            if message.text:
                new_msg = await client.send_message(
                    destination, 
                    formatted_text, 
                    reply_to_message_id=reply_to_id,
                    disable_web_page_preview=True
                )
            else:
                new_msg = await message.copy(
                    destination, 
                    caption=formatted_text,
                    reply_to_message_id=reply_to_id
                )
            
        # 10. Record in database
        await set_post(Post(
            destination=new_msg.chat.id,
            message_id=new_msg.id,
            source_channel_id=source_chat_id,
            source_message_id=source_msg_id,
            backup_id=message.id,
            reply_id=reply_to_id,
            message_text=formatted_text
        ))
        
        elapsed = (time.perf_counter() - start_time) * 1000
        logging.info(f"Processed and posted {source_chat_id}/{source_msg_id} to {destination} in {elapsed:.2f}ms")
        
    except Exception as e:
        logging.error(f"Failed to post message to {destination}: {e}")

async def handle_backup_message(client: Client, message: Message, cache):
    """Handles incoming messages from backup, including media group buffering."""
    if message.media_group_id:
        mg_id = message.media_group_id
        async with media_group_locks[mg_id]:
            media_groups[mg_id].append(message)
            if len(media_groups[mg_id]) == 1:
                await asyncio.sleep(2) 
                group = sorted(media_groups[mg_id], key=lambda m: m.id)
                await process_message_logic(client, group[0], cache, is_media_group=True)
                del media_groups[mg_id]
    else:
        await process_message_logic(client, message, cache, is_media_group=False)

async def main():
    add_logging()
    cache = get_cache()
    await cache.warm_cache()
    
    accounts = await get_accounts()
    if not accounts:
        logging.error("No accounts found")
        return
        
    a = accounts[0]
    logging.info(f"Starting Processor Account: {a.name} monitoring backup channel {CHANNEL_BACKUP}")
    
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
        await handle_backup_message(client, message, cache)

    await app.start()
    logging.info("Processor started and idling...")
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
