import asyncio
import logging
import os
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Final

from pyrogram import Client, filters, compose
from pyrogram.enums import ParseMode
from pyrogram.errors import MessageNotModified
from pyrogram.types import Message, InputMediaVideo, InputMediaPhoto, LinkPreviewOptions

from bot.db_cache import get_cache
from bot.destination import get_destination
from config import CHANNEL_BACKUP, CHANNEL_TEST, CHANNEL_UA, TESTING, PASSWORD, GROUP_LOG, CONTAINER
from db import get_source_ids_by_api_id, get_post, set_post, get_accounts
from extension.militarnyi import get_militarnyi
from extension.postillon import get_postillon
from model import Post
from translation import translate, debloat_text, format_text

processing_messages = defaultdict(asyncio.Lock)


async def get_message_lock(chat_id: int, message_id: int):
    """Get a lock for a specific message to prevent concurrent processing"""
    key = f"{chat_id}:{message_id}"
    return processing_messages[key]


def add_logging():
    if CONTAINER:
        logging.basicConfig(
            format="%(asctime)s %(levelname)-5s %(funcName)-20s [%(filename)s:%(lineno)d]: %(message)s",
            encoding="utf-8",
            level=logging.INFO,
            datefmt='%Y-%m-%d %H:%M:%S',
            handlers=[
                logging.StreamHandler(),
            ],
            force=True,
        )
    else:
        log_filename: Final[str] = rf"../logs/{datetime.now().strftime('%Y-%m-%d/%H-%M-%S')}.log"
        os.makedirs(os.path.dirname(log_filename), exist_ok=True)
        logging.basicConfig(
            format="%(asctime)s %(levelname)-5s %(funcName)-20s [%(filename)s:%(lineno)d]: %(message)s",
            encoding="utf-8",
            filename=log_filename,
            level=logging.INFO,
            datefmt='%Y-%m-%d %H:%M:%S',
            force=True,
        )

    logging.getLogger("httpx").setLevel(logging.WARNING)


async def backup_single(client: Client, message: Message) -> int:
    msg_backup = await client.forward_messages(CHANNEL_BACKUP, message.chat.id, message.id)
    logging.debug(f"Backup single: {msg_backup.link}")
    return msg_backup.id


async def backup_multiple(client: Client, messages: List[Message]) -> int:
    msg_ids = [message.id for message in messages]
    msg_backup = (await client.forward_messages(CHANNEL_BACKUP, messages[0].chat.id, msg_ids))[0]
    logging.debug(f"Backup multiple: {msg_backup.link}")
    return msg_backup.id


def listdirtree(start_path):
    for root, dirs, files in os.walk(start_path):
        level = root.replace(start_path, '').count(os.sep)
        indent = ' ' * 4 * level
        print(f'{indent}{os.path.basename(root)}/')
        sub_indent = ' ' * 4 * (level + 1)
        for f in files:
            print(f'{sub_indent}{f}')


async def main():
    add_logging()

    # Initialize cache at startup
    cache = get_cache()

    # Warm cache with commonly accessed data
    logging.info("Warming cache...")
    await cache.warm_cache()

    print(f"Running PRINT ...")
    logging.info(f"Running LOGGING ...")

    apps = list()
    accounts = await get_accounts()
    logging.info(f"Accounts: {accounts}")

    for a in accounts:
        print(f"Account {a.name} >>>>>")
        logging.info(f"Account {a.name} >>>>>")

        app = Client(
            name=a.name,
            api_id=a.api_id,
            api_hash=a.api_hash,
            phone_number=a.phone_number,
            password=PASSWORD,
            lang_code="de",
            parse_mode=ParseMode.HTML,
        )

        sources: List[int] = await get_source_ids_by_api_id(a.api_id)

        if not TESTING:
            remove_sources = [CHANNEL_TEST, -1001011817559, -1001123527809]
            for channel_id in remove_sources:
                while channel_id in sources:
                    sources.remove(channel_id)

        if TESTING and a.name == "Michael":
            @app.on_message(filters.caption & filters.chat([CHANNEL_TEST, -1001011817559]) & filters.photo)
            async def test_video(client: Client, message: Message):
                backup_id = await backup_single(client, message)

                cp = await get_militarnyi(message)
                source = await cache.get_source(message.chat.id)

                medias = list()
                for v in cp.video_urls:
                    medias.append(InputMediaVideo(v))
                for v in cp.image_urls:
                    medias.append(InputMediaPhoto(v))
                medias[0].caption = await format_text(translate(cp.caption), message, source, backup_id, cache)

                msg = (await client.send_media_group(CHANNEL_UA, medias))[0]

                for text in cp.texts:
                    text = await format_text(translate(text), message, source, backup_id, cache)
                    msg = await client.send_message(CHANNEL_UA, text, reply_to_message_id=msg.id,
                                                    disable_web_page_preview=True)

                for f in cp.image_urls + cp.video_urls:
                    Path(f).unlink(missing_ok=True)

        if TESTING and a.name == "Martin":
            @app.on_message(filters.chat([CHANNEL_TEST]) & filters.inline_keyboard)
            async def handle_postillion(client: Client, message: Message):
                print("postillon", message)

                backup_id = await backup_single(client, message)

                cp = await get_postillon(message)
                print("postillon")

                source = await cache.get_source(message.chat.id)

                msg = await client.send_photo(source.destination,
                                              cp.image_urls[0],
                                              await format_text(cp.caption, message, source, backup_id, cache))

        bf = filters.channel & filters.chat(sources) & ~filters.forwarded & filters.incoming
        mf = bf & (filters.photo | filters.video | filters.animation)

        @app.on_message(filters.text & bf)
        async def new_text(client: Client, message: Message):
            start_time = time.perf_counter()
            logging.info(f">>>>>> {client.name}: handle_text {message.chat.id, message.text.html}")

            # Check for duplicate message
            if cache.is_duplicate_message(message.chat.id, message.id):
                logging.info(f"Duplicate message detected, skipping: {message.chat.id}/{message.id}")
                return

            lock = await get_message_lock(message.chat.id, message.id)
            async with lock:
                text = await debloat_text(message, client, cache)
                if not text:
                    return
                logging.info(f"T X -single {text}")

                backup_id = await backup_single(client, message)

                # LLM routing - determines destination based on content (optimized)
                destination = await get_destination(text, message.chat.id, cache)
                if not destination:
                    logging.warning(f"No destination determined for message {message.chat.id}/{message.id}")
                    return

                source = await cache.get_source(message.chat.id)
                text = await format_text(text, message, source, backup_id, cache)

                if message.reply_to_message_id is not None:
                    reply_post = await get_post(message.chat.id, message.reply_to_message_id)
                    reply_id = reply_post.message_id if reply_post else None
                else:
                    reply_id = None

                logging.info(f"send New Text {client.name} to destination {destination}")
                msg = await client.send_message(destination, text,
                                                link_preview_options=LinkPreviewOptions(is_disabled=True))

                await set_post(Post(
                    msg.chat.id,
                    msg.id,
                    message.chat.id,
                    message.id,
                    backup_id,
                    reply_id,
                    text
                ))

                elapsed = (time.perf_counter() - start_time) * 1000
                if elapsed > 100:
                    logging.warning(f"Slow message processing: {elapsed:.2f}ms")

        @app.on_edited_message(filters.text & bf)
        async def edit_text(client: Client, message: Message):
            logging.info(f">>>>>> {client.name}: edit_text {message.chat.id, message.text.html}")

            if message.date < (datetime.now() - timedelta(weeks=1)):
                return

            await asyncio.sleep(60)

            post = await get_post(message.chat.id, message.id)
            if post is None:
                logging.info(f"Edit ignored - original post not found: {message.chat.id}/{message.id}")
                await new_text(client, message)
                return

            source = await cache.get_source(message.chat.id)
            text = await debloat_text(message, client, cache)
            if not text:
                return

            logging.info(f"edit text::: {post}")
            text = await format_text(text, message, source, post.backup_id, cache)

            try:
                await client.edit_message_text(post.destination, post.message_id, text,
                                               disable_web_page_preview=True)
            except MessageNotModified:
                pass

        @app.on_message(filters.media_group & filters.caption & mf)
        async def new_multiple(client: Client, message: Message):
            start_time = time.perf_counter()
            logging.info(f">>>>>> {client.name}: handle_multiple {message.chat.id, message.caption.html}")

            # Check for duplicate message
            if cache.is_duplicate_message(message.chat.id, message.id):
                logging.info(f"Duplicate message detected, skipping: {message.chat.id}/{message.id}")
                return

            lock = await get_message_lock(message.chat.id, message.id)
            async with lock:
                text = await debloat_text(message, client, cache)
                if not text:
                    return

                mg = await client.get_media_group(message.chat.id, message.id)
                backup_id = await backup_multiple(client, mg)

                source = await cache.get_source(message.chat.id)
                if not source.is_spread:
                    return

                # LLM routing - determines destination based on content (optimized)
                destination = await get_destination(text, message.chat.id, cache)
                if not destination:
                    logging.warning(f"No destination determined for message {message.chat.id}/{message.id}")
                    return

                text = await format_text(text, message, source, backup_id, cache)

                if message.reply_to_message_id is not None:
                    reply_post = await get_post(message.chat.id, message.reply_to_message_id)
                    reply_id = reply_post.message_id if reply_post else None
                else:
                    reply_id = None

                logging.info(f"send Media Group to destination {destination}")
                msgs = await client.copy_media_group(destination,
                                                     from_chat_id=message.chat.id,
                                                     message_id=message.id,
                                                     captions=text)

                await set_post(Post(
                    msgs[0].chat.id,
                    msgs[0].id,
                    message.chat.id,
                    message.id,
                    backup_id,
                    reply_id,
                    text
                ))

                elapsed = (time.perf_counter() - start_time) * 1000
                if elapsed > 100:
                    logging.warning(f"Slow media group processing: {elapsed:.2f}ms")

        @app.on_message(~filters.media_group & filters.caption & mf)
        async def new_single(client: Client, message: Message):
            start_time = time.perf_counter()
            logging.info(f">>>>>> {client.name}: handle_single {message.chat.id}")

            # Check for duplicate message
            if cache.is_duplicate_message(message.chat.id, message.id):
                logging.info(f"Duplicate message detected, skipping: {message.chat.id}/{message.id}")
                return

            lock = await get_message_lock(message.chat.id, message.id)
            async with lock:
                text = await debloat_text(message, client, cache)
                if not text:
                    return

                backup_id = await backup_single(client, message)
                source = await cache.get_source(message.chat.id)
                if not source.is_spread:
                    return

                # LLM routing - determines destination based on content (optimized)
                destination = await get_destination(text, message.chat.id, cache)
                if not destination:
                    logging.warning(f"No destination determined for message {message.chat.id}/{message.id}")
                    return

                logging.info(f">>>>>> {client.name}: handle_single destination={destination}")
                text = await format_text(text, message, source, backup_id, cache)

                if message.reply_to_message_id is not None:
                    reply_post = await get_post(message.chat.id, message.reply_to_message_id)
                    reply_id = reply_post.message_id if reply_post else None
                else:
                    reply_id = None

                logging.info(f"---- new single {client.name} to {destination}")
                msg = await message.copy(destination, caption=text)

                await set_post(Post(
                    msg.chat.id,
                    msg.id,
                    message.chat.id,
                    message.id,
                    backup_id,
                    reply_id,
                    text
                ))

                logging.info(f"----------------------------------------------------")

                elapsed = (time.perf_counter() - start_time) * 1000
                if elapsed > 100:
                    logging.warning(f"Slow single media processing: {elapsed:.2f}ms")

        @app.on_edited_message(filters.caption & mf)
        async def edit_caption(client: Client, message: Message):
            logging.info(f">>>>>> {client.name}: edit_caption {message.chat.id, message.caption.html}")

            if message.date < (datetime.now() - timedelta(weeks=1)):
                return

            post = await get_post(message.chat.id, message.id)
            if post is None:
                logging.warning(f"Edit ignored - original caption not found: {message.chat.id}/{message.id}")
                return

            text = await debloat_text(message, client, cache)
            if not text:
                return

            source = await cache.get_source(message.chat.id)
            text = await format_text(text, message, source, post.backup_id, cache)

            try:
                logging.info(f"edit_caption ::::::::::::::::::::: {post}")
                await client.edit_message_caption(post.destination, post.message_id, text)
            except MessageNotModified:
                pass

        @app.on_message(filters.command("refresh"))
        async def handle_refresh(client: Client, message: Message):
            """Refresh all caches from database"""
            logging.info(f"Refresh command from user {message.from_user.id} on account {client.name}")

            try:
                await cache.refresh_all()

                # Report cache statistics
                dest_count = len(cache.get_destination_regions())
                source_count = len(cache._sources)

                await message.reply_text(
                    f"✅ All caches refreshed successfully\n\n"
                    f"📊 Cache stats:\n"
                    f"• Sources: {source_count}\n"
                    f"• Destinations: {dest_count}\n"
                    f"• Regions: {', '.join(cache.get_destination_regions())}"
                )
                logging.info(f"All caches refreshed successfully for account {client.name}")
            except Exception as e:
                await message.reply_text(f"❌ Error refreshing cache: {str(e)}")
                logging.error(f"Error refreshing cache for account {client.name}: {e}", exc_info=True)

        @app.on_message(filters.command("join"))
        async def handle_join(client: Client, message: Message):
            args = message.text.split(" ")[1:]
            if len(args) < 2:
                await message.reply_text(f"❌ Usage: /join <channel> <username>")
                return

            joined_chat = args[0]
            joined_by = args[1]
            logging.info(f"join to {joined_chat} by user {joined_by} via {message.from_user.id}")

            try:
                chat = await client.join_chat(joined_chat)

                # Refresh sources cache to include the new channel
                logging.info(f"Refreshing sources cache after joining {joined_chat}")
                await cache.refresh_sources()

                source_count = len(cache._sources)
                await message.reply_text(
                    f"✅ Joined {joined_chat}\n"
                    f"✅ Sources cache refreshed ({source_count} sources)"
                )
                await client.send_message(GROUP_LOG, message_thread_id=489,
                                          text=f"Joined {joined_chat} by {joined_by}\n\n{chat}")
            except Exception as e:
                await message.reply_text(f"❌ Error joining {joined_chat}: {str(e)}")
                await client.send_message(GROUP_LOG, message_thread_id=489,
                                          text=f"ERROR Joining {joined_chat} by {joined_by}\n\n{e}")
                logging.error(f"Error joining chat: {e}", exc_info=True)

        @app.on_message(filters.command("leave"))
        async def handle_leave(client: Client, message: Message):
            logging.info(f"leave by user {message.from_user.id}")

            args = message.text.split(" ")[1:]
            if len(args) < 1:
                await message.reply_text(f"❌ Usage: /leave <channel_id>")
                return

            try:
                chat_id = args[0]
                chat = await client.leave_chat(chat_id)

                # Refresh sources cache to remove the left channel
                logging.info(f"Refreshing sources cache after leaving {chat_id}")
                await cache.refresh_sources()

                source_count = len(cache._sources)
                await message.reply_text(
                    f"✅ Left chat {chat_id}\n"
                    f"✅ Sources cache refreshed ({source_count} sources)\n\n"
                    f"Result: {chat}"
                )
            except Exception as e:
                await message.reply_text(f"❌ Error leaving chat: {str(e)}")
                logging.error(f"Error leaving chat: {e}", exc_info=True)

        apps.append(app)

    try:
        await compose(apps)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    asyncio.run(main())