import logging

import deepl
import regex as re
from deep_translator import GoogleTranslator
from deepl import QuotaExceededException, SplitSentences
from pyrogram import Client
from pyrogram.enums import ParseMode
from pyrogram.types import Message

from config import DEEPL, GROUP_PATTERN
from constant import (PLACEHOLDER, PATTERN_REPLACEMENT, PATTERN_HTMLTAG, PATTERN_HASHTAG, emoji_space_pattern,
                      emoji_pattern, PATTERN_FITZPATRICK, REPLACEMENTS, PATTERN_PARAGRAPH)
from model import SourceDisplay

translator = deepl.Translator(DEEPL)

BLACKLIST = [
    "Нічний чат, правила стандартні:",
    "paypal",
    "patreon"
]

BLACKLIST = re.compile(f"({')|('.join(BLACKLIST)})", re.IGNORECASE)

ABBREVIATIONS = {
    "AFU": "ukrainian Armed forces"
}

# Telegram caption limit - use conservative estimate for safety
TELEGRAM_CAPTION_LIMIT = 1024
# Reserve space for footer (approximate max size)
FOOTER_RESERVE = 200


def escape(string: str) -> str:
    return re.escape(string)


def chunk_paragraphs(text: str) -> str:
    if len(text) <= 1200 and len(re.findall(f'\n\n', text)) < 5:
        return text

    res = []
    threshold = 440
    for chunk in re.split(PATTERN_PARAGRAPH, text):
        if res and len(chunk) + len(res[-1]) < threshold:
            res[-1] += f' {chunk}'
        else:
            res.append(f'{chunk}')
    return "\n\n".join(res)


def truncate_text(text: str, max_length: int) -> str:
    """
    Simple truncation at sentence boundary. Fast and efficient.
    """
    if len(text) <= max_length:
        return text

    # Reserve space for ellipsis
    max_length -= 4
    truncated = text[:max_length]

    # Find last sentence ending
    last_end = max(
        truncated.rfind('.'),
        truncated.rfind('!'),
        truncated.rfind('?'),
        truncated.rfind('\n\n')
    )

    # Use sentence boundary if reasonable, otherwise hard cut
    if last_end > max_length * 0.6:
        truncated = truncated[:last_end + 1]

    return truncated.rstrip() + " ..."


def translate(text: str, is_caption: bool = False) -> str:
    """
    Translate text. If is_caption=True, pre-truncate to avoid translating excess text.
    """
    # Pre-truncate long captions before translation to save API calls
    if is_caption and len(text) > TELEGRAM_CAPTION_LIMIT - FOOTER_RESERVE:
        logging.info(
            f"Pre-truncating caption before translation: {len(text)} -> {TELEGRAM_CAPTION_LIMIT - FOOTER_RESERVE}")
        text = truncate_text(text, TELEGRAM_CAPTION_LIMIT - FOOTER_RESERVE)

    try:
        translated_text = translator.translate_text(text,
                                                    target_lang="de",
                                                    split_sentences=SplitSentences.ALL,
                                                    tag_handling="html",
                                                    ).text

    except QuotaExceededException:
        logging.info("--- Quota exceeded ---")
        translated_text = GoogleTranslator(source='auto', target="de").translate(text=text)
    except Exception as e:
        logging.error(f"--- other error translating --- {e}")
        translated_text = GoogleTranslator(source='auto', target="de").translate(text=text)

    translated_text = chunk_paragraphs(translated_text)

    # Post-translation safety check for captions
    if is_caption and len(translated_text) > TELEGRAM_CAPTION_LIMIT - FOOTER_RESERVE:
        logging.warning(f"Post-translation truncation needed: {len(translated_text)} chars")
        translated_text = truncate_text(translated_text, TELEGRAM_CAPTION_LIMIT - FOOTER_RESERVE)

    logging.info(f"paragraphed ::::::: {translated_text}")

    return translated_text


from bot.db_cache import DBCache


async def format_text(text: str, message: Message, source: SourceDisplay, backup_id: int,
                      footer: str | None = None) -> str:
    """
    Format text with footer. Simple and fast - no length checking here.
    Length checking should be done before calling this function.
    """
    formatted = f"{text}\n\nQuelle: <a href='{message.link}'>{source.display_name}"
    if source.bias is not None:
        formatted += f" {source.bias}"
    formatted += f"</a> |<a href='https://t.me/nn_backup/{backup_id}'> 💾 </a>"

    if source.username is None and source.invite is not None:
        formatted += f"|<a href='https://t.me/+{source.invite}'> 🔗️ </a>"

    if source.detail_id is not None:
        formatted += f"|<a href='https://t.me/nn_sources/{source.detail_id}'> ℹ️ </a>"

    if footer is not None:
        formatted += footer

    return formatted


async def debloat_message(message: Message, client: Client, cache: DBCache) -> bool | str:
    if message.caption is not None:
        limit = 20
        text = message.caption.html
    else:
        text = message.text.html
        limit = 30

    if len(re.findall(BLACKLIST, text)) != 0:
        return False

    # Use cached patterns instead of direct db call
    patterns = await cache.get_patterns(message.chat.id)

    text = PATTERN_HTMLTAG.sub("", text).rstrip()

    if patterns is not None and len(patterns) != 0:
        pat = [escape(re.sub(PATTERN_HTMLTAG, '', p)) for p in patterns]
        pattern = fr"({')|('.join(pat)})"

        result = re.findall(pattern, text, flags=re.IGNORECASE)
        logging.info(f"Text ::: {text}")
        logging.info(f"clean_pattern__result ::: {result}")
        logging.info(pattern)

        if len(result) == 0:
            await message.forward(GROUP_PATTERN)
            await client.send_message(GROUP_PATTERN, text, parse_mode=ParseMode.DISABLED)

            logging.info(f"-- doesnt match --\n\n{text}\n\n---")
            return False

        text = re.sub(pattern, "", text)

    text = re.sub(f"@{message.chat.username}$", "", text, flags=re.IGNORECASE).rstrip()
    text = PATTERN_HASHTAG.sub("", text, ).rstrip()
    logging.info(f"<<<<< hashtag :  {text}")

    if len(re.findall(r"t\.me/\+", text)) != 0:
        logging.info(f">>>>>>>>> likely contains ad, please check! -- {message.link}")
        await client.send_message(GROUP_PATTERN, f">>>>>>>>> likely contains ad, please check! -- {message.link}",
                                  parse_mode=ParseMode.DISABLED)
        return False

    if len(text) < limit:
        logging.info(f"Text too short - limit: {limit}")
        return False

    return text


async def debloat_text(message: Message, client: Client, cache: DBCache, is_caption: bool = False) -> bool | str:
    """
    Process and translate text. Pass is_caption=True to enable length limits.
    """
    text = await debloat_message(message, client, cache)

    if not text:
        return False

    logging.info(f"clean_pattern  {text}")

    text = re.sub(emoji_space_pattern, r"\1 \2", text)
    logging.info(f"<<<<< spaced  {text}")

    emojis = emoji_pattern.findall(text)
    logging.info(f">>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>> emoijis: {emojis}")
    text = emoji_pattern.sub(PLACEHOLDER, text).rstrip()
    logging.info(f">>>>>>>>>> placeholder  {text}")

    text = PATTERN_REPLACEMENT.sub(lambda m: REPLACEMENTS[re.escape(m.group(0))], text)

    logging.info(f"<<<<< spaced_BEFORE::: {text}", )

    for abbreviation, meaning in ABBREVIATIONS.items():
        text = re.sub(r'\b' + re.escape(abbreviation) + r'\b', meaning, text, flags=re.IGNORECASE)

    # Translate with caption awareness - truncates before translation if needed
    text = translate(text, is_caption=is_caption)

    logging.info(f"--------------------------------------------------------\n\n------ TRANS -single {text, emojis}", )

    for emoji in emojis:
        text = re.sub(PLACEHOLDER, emoji, text, 1)

    logging.info(f"-------------------------\n>>>>>>>> translated_text:\n {text}")

    text = re.sub(PATTERN_FITZPATRICK, "", text, ).rstrip()

    return text