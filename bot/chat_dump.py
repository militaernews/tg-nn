import json
from datetime import datetime, timezone
from io import BytesIO

from pyrogram import Client, raw, utils

from bot.db_cache import DBCache

# Telegram's cap on joined channels + supergroups for accounts without Premium (doubled with Premium).
CHANNEL_LIMIT = 500
MIN_FREE_SLOTS = 10


async def _dialogs(client: Client) -> raw.types.messages.Dialogs:
    """All dialogs as raw objects: unlike pyrogram's Chat, raw Channel keeps admin_rights."""
    users, chats, dialogs = {}, {}, []
    offset_date, offset_id, offset_peer = 0, 0, raw.types.InputPeerEmpty()
    while True:
        r = await client.invoke(raw.functions.messages.GetDialogs(
            offset_date=offset_date, offset_id=offset_id, offset_peer=offset_peer, limit=100, hash=0))
        users.update({u.id: u for u in r.users})
        chats.update({c.id: c for c in r.chats})
        dialogs += r.dialogs
        if isinstance(r, raw.types.messages.Dialogs) or len(r.dialogs) < 100:
            return users, chats, dialogs
        last = r.dialogs[-1]
        peer_id = utils.get_peer_id(last.peer)
        top = next((m for m in r.messages
                    if getattr(m, "peer_id", None) and utils.get_peer_id(m.peer_id) == peer_id
                    and m.id == last.top_message), None)
        offset_date, offset_id = (top.date if top else 0), last.top_message
        offset_peer = await client.resolve_peer(peer_id)


def _chat_entry(peer_id: int, users: dict, chats: dict) -> dict:
    if peer_id > 0:
        u = users.get(peer_id)
        return {"id": peer_id, "type": "bot" if u and u.bot else "private",
                "title": " ".join(filter(None, [u.first_name, u.last_name])) if u else None,
                "username": u.username if u else None}

    raw_id = utils.get_raw_peer_id(peer_id)
    c = chats.get(raw_id)
    if isinstance(c, raw.types.Channel):
        return {"id": peer_id, "type": "channel" if c.broadcast else "supergroup", "title": c.title,
                "username": c.username or (c.usernames[0].username if c.usernames else None),
                "members": c.participants_count, "creator": bool(c.creator),
                "admin": bool(c.creator or c.admin_rights), "left": bool(c.left)}
    if isinstance(c, raw.types.Chat):
        return {"id": peer_id, "type": "group", "title": c.title, "members": c.participants_count,
                "creator": bool(c.creator), "admin": bool(c.creator or c.admin_rights), "left": bool(c.left)}
    return {"id": peer_id, "type": "unavailable", "title": getattr(c, "title", None)}


async def dump_chats(client: Client, cache: DBCache, account_name: str) -> tuple[BytesIO, str]:
    """Dump every chat the account is in, annotated with its tg-nn source config, as a JSON file."""
    me = await client.get_me()
    users, chats, dialogs = await _dialogs(client)

    entries = []
    for d in dialogs:
        entry = _chat_entry(utils.get_peer_id(d.peer), users, chats)
        source = await cache.get_source(entry["id"])
        if source:
            entry["source"] = {"active": source.is_active, "spread": source.is_spread,
                               "destination": source.destination}
        entries.append(entry)

    slot_users = [e for e in entries if e["type"] in ("channel", "supergroup") and not e.get("left")]
    limit = CHANNEL_LIMIT * 2 if me.is_premium else CHANNEL_LIMIT
    free = limit - len(slot_users)

    file = BytesIO(json.dumps(entries, ensure_ascii=False, indent=1).encode())
    file.name = f"chats_{account_name}_{datetime.now(timezone.utc):%Y-%m-%d_%H%M}.json"

    count = lambda t: sum(e["type"] == t for e in entries)
    caption = (
        f"<b>{account_name}</b> ({me.id}): {len(entries)} chats\n"
        f"{count('channel')} channels, {count('supergroup')} supergroups, {count('group')} groups, "
        f"{count('private')} private, {count('bot')} bots\n"
        f"Slots: {len(slot_users)}/{limit} used, {free} free"
        f"{' ⚠️ below minimum of ' + str(MIN_FREE_SLOTS) if free < MIN_FREE_SLOTS else ''}\n"
        f"{sum('source' in e for e in entries)} tg-nn sources, "
        f"{sum(e.get('source', {}).get('active', False) for e in entries)} active"
    )
    return file, caption
