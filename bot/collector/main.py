import asyncio
import logging
from datetime import datetime
from typing import List, Final
import os

from pyrogram import Client, filters, compose
from pyrogram.enums import ParseMode
from pyrogram.types import Message

from bot.config import CHANNEL_BACKUP, PASSWORD, CONTAINER
from bot.db import get_accounts, get_source_ids_by_api_id
from bot.db_cache import get_cache

def add_logging():
    level = logging.INFO
    format_str = "%(asctime)s %(levelname)-5s %(funcName)-20s [%(filename)s:%(lineno)d]: %(message)s"
    date_fmt = '%Y-%m-%d %H:%M:%S'
    
    if CONTAINER:
        logging.basicConfig(format=format_str, level=level, datefmt=date_fmt, handlers=[logging.StreamHandler()], force=True)
    else:
        log_filename = f"logs/collector_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        os.makedirs("logs", exist_ok=True)
        logging.basicConfig(format=format_str, level=level, datefmt=date_fmt, filename=log_filename, force=True)
    
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("pyrogram").setLevel(logging.WARNING)

async def backup_message(client: Client, message: Message) -> int:
    """Forward message to backup channel and return the backup message ID."""
    try:
        # Forwarding handles media groups automatically if we pass the message ID
        # But for simplicity and reliability in the collector, we forward each message.
        # The processor will handle grouping if needed.
        msg_backup = await client.forward_messages(CHANNEL_BACKUP, message.chat.id, message.id)
        # If it was a media group, forward_messages returns a list
        backup_id = msg_backup[0].id if isinstance(msg_backup, list) else msg_backup.id
        logging.info(f"Forwarded {message.chat.id}/{message.id} to backup {CHANNEL_BACKUP}/{backup_id}")
        return backup_id
    except Exception as e:
        logging.error(f"Failed to forward message {message.chat.id}/{message.id}: {e}")
        return None

async def main():
    add_logging()
    cache = get_cache()
    await cache.warm_cache()
    
    accounts = await get_accounts()
    apps = []
    
    for a in accounts:
        logging.info(f"Starting Collector Account: {a.name}")
        app = Client(
            name=f"collector_{a.name}",
            api_id=a.api_id,
            api_hash=a.api_hash,
            phone_number=a.phone_number,
            password=PASSWORD,
            parse_mode=ParseMode.HTML,
        )
        
        # Get active sources for this account
        sources = await get_source_ids_by_api_id(a.api_id)
        if not sources:
            logging.warning(f"No active sources for account {a.name}")
            continue
            
        logging.info(f"Account {a.name} monitoring {len(sources)} sources")
        
        # Filter for incoming messages from active sources
        source_filter = filters.chat(sources) & ~filters.forwarded & filters.incoming
        
        @app.on_message(source_filter)
        async def handle_incoming(client: Client, message: Message):
            # Check if source is active in cache
            source = await cache.get_source(message.chat.id)
            if not source or not source.is_active:
                return
                
            # Simply forward to backup
            await backup_message(client, message)

        apps.append(app)
        
    if apps:
        await compose(apps)
    else:
        logging.error("No accounts to start")

if __name__ == "__main__":
    asyncio.run(main())
