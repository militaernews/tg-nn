import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import List, Final, Optional
import os

from pyrogram import Client, filters, compose
from pyrogram.enums import ParseMode
from pyrogram.errors import MessageNotModified
from pyrogram.types import Message, LinkPreviewOptions

from bot.config import CHANNEL_BACKUP, PASSWORD, CONTAINER, GROUP_LOG
from bot.db import get_accounts, get_post, set_post
from bot.db_cache import get_cache
from bot.destination import get_destination
from bot.model import Post
from bot.translation import debloat_text, format_text

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

async def process_backup_message(client: Client, message: Message, cache):
    """Process a message from the backup channel and route it to a destination."""
    start_time = time.perf_counter()
    
    # 1. Identify original source from forward info
    if not message.forward_from_chat:
        logging.warning(f"Message {message.id} in backup is not a forward, skipping")
        return
        
    source_chat_id = message.forward_from_chat.id
    source_msg_id = message.forward_from_message_id
    
    # 2. Check if already posted (Database lookup)
    existing_post = await get_post(source_chat_id, source_msg_id)
    if existing_post:
        logging.info(f"Message {source_chat_id}/{source_msg_id} already posted to {existing_post.destination}, skipping")
        return
        
    # 3. Check if source should be spread
    source = await cache.get_source(source_chat_id)
    if not source or not source.is_spread:
        logging.info(f"Source {source_chat_id} is not marked for spreading, skipping")
        return
        
    # 4. Debloat and prepare text
    # Note: debloat_text might need adjustment to handle forwarded messages correctly
    text = await debloat_text(message, client, cache, is_caption=bool(message.caption))
    if not text:
        logging.info(f"Message {source_chat_id}/{source_msg_id} has no text after debloating, skipping")
        return
        
    # 5. Content-based routing (LLM)
    destination = await get_destination(text, source_chat_id, cache)
    if not destination:
        logging.warning(f"No destination determined for message {source_chat_id}/{source_msg_id}")
        return
        
    # 6. Format with footer
    footer = await cache.get_footer(destination)
    formatted_text = await format_text(text, message, source, message.id, footer)
    
    # 7. Post to destination
    try:
        if message.media_group_id:
            # Handle media groups: this is tricky because we get them one by one from backup
            # For now, we copy the single message. A more robust way would be to wait for the group.
            # But since they are forwarded individually to backup, we copy them individually.
            # If the user wants them grouped, we'd need a buffer here.
            msg = await message.copy(destination, caption=formatted_text)
        elif message.text:
            msg = await client.send_message(destination, formatted_text, 
                                            link_preview_options=LinkPreviewOptions(is_disabled=True))
        else:
            msg = await message.copy(destination, caption=formatted_text)
            
        # 8. Record in database
        await set_post(Post(
            destination=msg.chat.id,
            message_id=msg.id,
            source_channel_id=source_chat_id,
            source_message_id=source_msg_id,
            backup_id=message.id,
            reply_id=None, # Handling replies across backup is complex
            message_text=formatted_text
        ))
        
        elapsed = (time.perf_counter() - start_time) * 1000
        logging.info(f"Processed and posted {source_chat_id}/{source_msg_id} to {destination} in {elapsed:.2f}ms")
        
    except Exception as e:
        logging.error(f"Failed to post message to {destination}: {e}")

async def main():
    add_logging()
    cache = get_cache()
    await cache.warm_cache()
    
    accounts = await get_accounts()
    if not accounts:
        logging.error("No accounts found")
        return
        
    # Use the first account as the processor for the backup channel
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
    async def handle_backup(client: Client, message: Message):
        await process_backup_message(client, message, cache)

    # Also handle edits if needed
    @app.on_edited_message(filters.chat(CHANNEL_BACKUP))
    async def handle_backup_edit(client: Client, message: Message):
        # Logic for edits could be added here
        pass

    await app.start()
    logging.info("Processor started and idling...")
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
