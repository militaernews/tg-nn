import logging
import sys
from contextlib import asynccontextmanager
from dataclasses import fields
from functools import wraps
from os import getenv
from typing import Dict, Union, List, Optional, Callable, Awaitable, Any

from asyncpg import Pool, create_pool, Connection, Record

from bot.config import DATABASE_URL
from bot.model import Account, Source, SourceDisplay, Post, Destination


def record_to_dataclass(record: Record, dataclass_type: Any) -> Any:
    """Convert asyncpg Record to dataclass."""
    if record is None:
        return None
    field_names = {f.name for f in fields(dataclass_type)}
    values = {name: record[name] for name in field_names if name in record}
    return dataclass_type(**values)


class DBPool:
    _pool: Optional[Pool] = None

    @classmethod
    def is_test(cls) -> bool:
        return "pytest" in sys.modules or getenv("TESTING") == "true"

    @classmethod
    async def get_pool(cls) -> Pool:
        if cls._pool is None:
            cls._pool = await create_pool(
                DATABASE_URL,
                min_size=0,                           # no persistent connection → Neon can scale to zero
                max_size=5,                           # cap concurrent connections
                max_inactive_connection_lifetime=60,  # release idle connections after 60s
            )
        return cls._pool

    @classmethod
    async def close_pool(cls) -> None:
        """Close the pool so Neon can suspend the compute promptly."""
        if cls._pool is not None:
            await cls._pool.close()
            cls._pool = None
            logging.info("DB pool closed")

    @classmethod
    @asynccontextmanager
    async def connection(cls):
        if cls.is_test():
            yield None
            return
        pool = await cls.get_pool()
        async with pool.acquire() as conn:
            yield conn


def db(func: Callable[..., Awaitable]):
    """Decorator that injects a DB connection as `conn` kwarg."""
    @wraps(func)
    async def wrapper(*args, **kwargs):
        async with DBPool.connection() as conn:
            if DBPool.is_test():
                return await func(*args, **kwargs)
            kwargs["conn"] = conn
            return await func(*args, **kwargs)
    return wrapper


# ── Queries ───────────────────────────────────────────────────────────────────

@db
async def get_source_ids_by_api_id(api_id: int, conn: Connection) -> List[int]:
    records: List[Record] = await conn.fetch(
        "SELECT channel_id FROM sources WHERE api_id = $1 AND is_active = TRUE;",
        api_id,
    )
    return [r["channel_id"] for r in records]


@db
async def get_patterns(channel_id: int, conn: Connection) -> List[str]:
    records: List[Record] = await conn.fetch(
        "SELECT pattern FROM bloats WHERE channel_id = $1;",
        channel_id,
    )
    return [r[0] for r in records]


@db
async def get_source(channel_id: int, conn: Connection) -> Optional[SourceDisplay]:
    record: Record = await conn.fetchrow(
        "SELECT * FROM sources WHERE channel_id = $1;",
        channel_id,
    )
    if record is None:
        return None
    sd: SourceDisplay = record_to_dataclass(record, SourceDisplay)
    if not sd.display_name:
        sd.display_name = record["channel_name"]
    return sd


@db
async def get_sources(conn: Connection) -> Dict[int, SourceDisplay]:
    records: List[Record] = await conn.fetch("SELECT * FROM sources;")
    result: Dict[int, SourceDisplay] = {}
    for r in records:
        sd = record_to_dataclass(r, SourceDisplay)
        if not sd.display_name:
            sd.display_name = r["channel_name"]
        result[r["channel_id"]] = sd
    return result


@db
async def get_footer(channel_id: int, conn: Connection) -> Optional[str]:
    return await conn.fetchval(
        "SELECT footer FROM destinations WHERE channel_id = $1;",
        channel_id,
    )


@db
async def set_sources(sources: Dict[int, Dict[str, Union[str, int]]], conn: Connection) -> None:
    field_names = [f.name for f in fields(Source)]
    s_input = []
    b_input = []

    for k, v in sources.items():
        row = [k] + [v.get(f, None) for f in field_names[1:]]
        s_input.append(row)
        if "bloat" in v:
            b_input.extend([k, bloat] for bloat in v["bloat"])

    col = ", ".join(field_names)
    placeholders = ", ".join(f"${i+1}" for i in range(len(field_names)))

    await conn.executemany(
        f"INSERT INTO sources ({col}) VALUES ({placeholders});",
        s_input,
    )
    await conn.executemany(
        "INSERT INTO bloats (channel_id, pattern) VALUES ($1, $2);",
        b_input,
    )


@db
async def set_post(post: Post, conn: Connection) -> None:
    await conn.execute(
        """INSERT INTO posts
           (destination, message_id, source_channel_id, source_message_id,
            backup_id, reply_id, message_text, file_id)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8);""",
        post.destination, post.message_id, post.source_channel_id,
        post.source_message_id, post.backup_id, post.reply_id,
        post.message_text, post.file_id,
    )


@db
async def get_post(source_channel_id: int, source_message_id: int, conn: Connection) -> Optional[Post]:
    record: Record = await conn.fetchrow(
        "SELECT * FROM posts WHERE source_channel_id = $1 AND source_message_id = $2;",
        source_channel_id, source_message_id,
    )
    return record_to_dataclass(record, Post)


@db
async def set_destination(destination: Destination, conn: Connection) -> None:
    await conn.execute(
        "INSERT INTO destinations (channel_id, name, group_id) VALUES ($1, $2, $3);",
        destination.channel_id, destination.name, destination.group_id,
    )


@db
async def get_destinations(conn: Connection) -> List[Destination]:
    records: List[Record] = await conn.fetch("SELECT * FROM destinations;")
    return [record_to_dataclass(r, Destination) for r in records]


@db
async def get_accounts(conn: Connection) -> List[Account]:
    records: List[Record] = await conn.fetch("SELECT * FROM accounts;")
    return [record_to_dataclass(r, Account) for r in records]