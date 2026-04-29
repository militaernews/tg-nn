import asyncio
import logging
from unittest.mock import patch, AsyncMock

import pytest
import pytest_asyncio
from asyncpg import connect

from bot.config import DATABASE_URL_TEST
from bot.db import get_accounts

logging.basicConfig(level=logging.DEBUG)

pytestmark = pytest.mark.asyncio  # applies to every async test in this file


# ── Event loop ────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def event_loop():
    policy = asyncio.get_event_loop_policy()
    loop = policy.new_event_loop()
    yield loop
    loop.close()


# ── DB fixtures ───────────────────────────────────────────────────────────────

@pytest_asyncio.fixture(loop_scope="function")
async def db():
    """Fresh schema for every test function."""
    conn = await connect(DATABASE_URL_TEST)

    await conn.execute("DROP TABLE IF EXISTS posts CASCADE")
    await conn.execute("DROP TABLE IF EXISTS bloats CASCADE")
    await conn.execute("DROP TABLE IF EXISTS sources CASCADE")
    await conn.execute("DROP TABLE IF EXISTS destinations CASCADE")
    await conn.execute("DROP TABLE IF EXISTS accounts CASCADE")

    await conn.execute("""
        CREATE TABLE accounts (
            api_id      BIGINT       NOT NULL,
            api_hash    TEXT         NOT NULL,
            user_id     BIGINT       NOT NULL,
            name        VARCHAR(20)  NOT NULL,
            phone_number VARCHAR(14) NOT NULL,
            description TEXT,
            PRIMARY KEY (api_id)
        )
    """)
    await conn.execute("""
        CREATE TABLE destinations (
            channel_id BIGINT       NOT NULL,
            name       VARCHAR(128) NOT NULL,
            group_id   BIGINT,
            footer     TEXT,
            PRIMARY KEY (channel_id)
        )
    """)
    await conn.execute("""
        CREATE TABLE sources (
            channel_id   BIGINT       NOT NULL,
            channel_name VARCHAR(128) NOT NULL,
            bias         TEXT,
            display_name VARCHAR(128),
            invite       VARCHAR(20),
            username     VARCHAR(32),
            api_id       BIGINT,
            description  TEXT,
            rating       INT,
            destination  BIGINT,
            detail_id    INT,
            is_spread    BOOLEAN DEFAULT TRUE,
            is_active    BOOLEAN DEFAULT FALSE,
            PRIMARY KEY (channel_id),
            CONSTRAINT fk_destination FOREIGN KEY (destination) REFERENCES destinations (channel_id),
            CONSTRAINT fk_account     FOREIGN KEY (api_id)      REFERENCES accounts    (api_id)
        )
    """)
    await conn.execute("""
        CREATE TABLE bloats (
            channel_id BIGINT NOT NULL,
            pattern    TEXT   NOT NULL,
            PRIMARY KEY (channel_id, pattern),
            CONSTRAINT fk_channel FOREIGN KEY (channel_id) REFERENCES sources (channel_id)
        )
    """)
    await conn.execute("""
        CREATE TABLE posts (
            destination        BIGINT NOT NULL,
            message_id         INT    NOT NULL,
            source_channel_id  BIGINT NOT NULL,
            source_message_id  INT    NOT NULL,
            backup_id          INT    NOT NULL,
            reply_id           INT,
            message_text       TEXT,
            file_id            BIGINT,
            PRIMARY KEY (source_channel_id, source_message_id),
            CONSTRAINT fk_channel     FOREIGN KEY (source_channel_id) REFERENCES sources      (channel_id),
            CONSTRAINT fk_destination FOREIGN KEY (destination)       REFERENCES destinations (channel_id)
        )
    """)

    yield conn
    await conn.close()


@pytest_asyncio.fixture(loop_scope="function")
async def sample_data(db):
    """Minimal seed data. Uses unique IDs that don't clash with db fixture."""
    await db.execute(
        "INSERT INTO accounts (api_id, api_hash, user_id, name, phone_number, description) "
        "VALUES ($1, $2, $3, $4, $5, $6)",
        12345, "test_hash", 67890, "TestUser", "+1234567890", "Test account",
    )
    await db.execute(
        "INSERT INTO destinations (channel_id, name, group_id, footer) VALUES ($1, $2, $3, $4)",
        -1001111111111, "Test Destination", -1002222222222, "Test Footer",
    )
    await db.execute(
        """INSERT INTO sources
           (channel_id, channel_name, bias, display_name, invite, username,
            api_id, description, rating, destination, detail_id, is_spread, is_active)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)""",
        -1003333333333, "Test Channel", "neutral", "Test Display", "test_invite",
        "test_username", 12345, "Test description", 5, -1001111111111, 1, True, True,
    )
    await db.execute(
        "INSERT INTO bloats (channel_id, pattern) VALUES ($1, $2)",
        -1003333333333, "spam_pattern",
    )
    await db.execute(
        """INSERT INTO posts
           (destination, message_id, source_channel_id, source_message_id,
            backup_id, reply_id, message_text, file_id)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8)""",
        -1001111111111, 100, -1003333333333, 200, 300, 400, "Test message", 500,
    )
    yield db


# ── Schema integrity ──────────────────────────────────────────────────────────

class TestSchemaIntegrity:

    async def test_all_tables_exist(self, db):
        for table in ("accounts", "destinations", "sources", "bloats", "posts"):
            exists = await db.fetchval(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = $1)",
                table,
            )
            assert exists, f"Table '{table}' should exist"

    async def test_foreign_key_constraints(self, sample_data):
        with pytest.raises(Exception):
            await sample_data.execute(
                "INSERT INTO sources (channel_id, channel_name, destination) VALUES ($1, $2, $3)",
                -9999, "Invalid Source", -8888,
            )

    async def test_primary_key_constraints(self, sample_data):
        with pytest.raises(Exception):
            await sample_data.execute(
                "INSERT INTO accounts (api_id, api_hash, user_id, name, phone_number) "
                "VALUES ($1, $2, $3, $4, $5)",
                12345, "another_hash", 11111, "Another", "+9876543210",
            )


# ── DB functions ──────────────────────────────────────────────────────────────

class TestDatabaseFunctions:

    async def test_get_source_ids_by_api_id(self, sample_data):
        result = await sample_data.fetch(
            "SELECT channel_id FROM sources WHERE api_id = $1 AND is_active = TRUE",
            12345,
        )
        assert len(result) == 1
        assert result[0]["channel_id"] == -1003333333333

    async def test_get_patterns(self, sample_data):
        result = await sample_data.fetch(
            "SELECT pattern FROM bloats WHERE channel_id = $1",
            -1003333333333,
        )
        assert len(result) == 1
        assert result[0]["pattern"] == "spam_pattern"

    async def test_get_source(self, sample_data):
        result = await sample_data.fetchrow(
            "SELECT * FROM sources WHERE channel_id = $1",
            -1003333333333,
        )
        assert result is not None
        assert result["channel_name"] == "Test Channel"
        assert result["display_name"] == "Test Display"
        assert result["bias"] == "neutral"

    async def test_get_footer(self, sample_data):
        result = await sample_data.fetchval(
            "SELECT footer FROM destinations WHERE channel_id = $1",
            -1001111111111,
        )
        assert result == "Test Footer"

    async def test_set_post(self, sample_data):
        await sample_data.execute(
            """INSERT INTO posts
               (destination, message_id, source_channel_id, source_message_id,
                backup_id, reply_id, message_text, file_id)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8)""",
            -1001111111111, 101, -1003333333333, 201, 301, None, "Another test message", None,
        )
        result = await sample_data.fetchrow(
            "SELECT * FROM posts WHERE source_channel_id = $1 AND source_message_id = $2",
            -1003333333333, 201,
        )
        assert result is not None
        assert result["message_text"] == "Another test message"
        assert result["message_id"] == 101

    async def test_get_post(self, sample_data):
        result = await sample_data.fetchrow(
            "SELECT * FROM posts WHERE source_channel_id = $1 AND source_message_id = $2",
            -1003333333333, 200,
        )
        assert result is not None
        assert result["message_text"] == "Test message"
        assert result["file_id"] == 500
        assert result["reply_id"] == 400


# ── Data integrity ────────────────────────────────────────────────────────────

class TestDataIntegrity:

    async def test_varchar_length_constraints(self, db):
        # Need a destination first so the FK on sources resolves — but this
        # test only inserts into accounts, so no FK needed here.
        with pytest.raises(Exception):
            await db.execute(
                "INSERT INTO accounts (api_id, api_hash, user_id, name, phone_number) "
                "VALUES ($1, $2, $3, $4, $5)",
                99999, "hash", 12345, "A" * 25, "+1234567890",  # name > VARCHAR(20)
            )

    async def test_default_values(self, db):
        # Use IDs that don't exist yet in this fresh db fixture
        await db.execute(
            "INSERT INTO destinations (channel_id, name) VALUES ($1, $2)",
            -2001111111111, "Test Dest",
        )
        await db.execute(
            "INSERT INTO accounts (api_id, api_hash, user_id, name, phone_number) "
            "VALUES ($1, $2, $3, $4, $5)",
            22345, "hash", 67891, "Tst2", "+1234567891",
        )
        await db.execute(
            "INSERT INTO sources (channel_id, channel_name) VALUES ($1, $2)",
            -2003333333333, "Default Test Channel",
        )
        result = await db.fetchrow(
            "SELECT is_spread, is_active FROM sources WHERE channel_id = $1",
            -2003333333333,
        )
        assert result["is_spread"] is True
        assert result["is_active"] is False


# ── Complex queries ───────────────────────────────────────────────────────────

class TestComplexQueries:

    async def test_join_operations(self, sample_data):
        result = await sample_data.fetchrow("""
            SELECT s.channel_name, d.name AS dest_name, d.footer
            FROM   sources s
            JOIN   destinations d ON s.destination = d.channel_id
            WHERE  s.channel_id = $1
        """, -1003333333333)
        assert result is not None
        assert result["channel_name"] == "Test Channel"
        assert result["dest_name"] == "Test Destination"
        assert result["footer"] == "Test Footer"

    async def test_aggregation_queries(self, sample_data):
        result = await sample_data.fetchrow("""
            SELECT destination, COUNT(*) AS post_count
            FROM   posts
            GROUP  BY destination
            HAVING destination = $1
        """, -1001111111111)
        assert result is not None
        assert result["post_count"] >= 1


# ── Standalone tests ──────────────────────────────────────────────────────────

async def test_database_connection_failure():
    with patch("asyncpg.connect", side_effect=Exception("Connection failed")):
        with pytest.raises(Exception, match="Connection failed"):
            await connect("invalid_url")


async def test_transaction_handling(sample_data):
    async with sample_data.transaction():
        await sample_data.execute(
            "INSERT INTO destinations (channel_id, name) VALUES ($1, $2)",
            -1006666666666, "Transaction Test",
        )

    result = await sample_data.fetchval(
        "SELECT name FROM destinations WHERE channel_id = $1",
        -1006666666666,
    )
    assert result == "Transaction Test"


async def test_get_accounts_via_db_function(sample_data):
    """Test get_accounts() through the @db decorator (mocks the pool)."""
    # The @db decorator injects conn from the pool. In tests, DBPool.is_test()
    # returns True so the decorator passes conn=None and the function is called
    # without a connection — we mock at the asyncpg level instead.
    with patch("bot.db.DBPool.connection") as mock_ctx:
        mock_ctx.return_value.__aenter__ = AsyncMock(return_value=sample_data)
        mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        # Temporarily make is_test() return False so the decorator injects conn
        with patch("bot.db.DBPool.is_test", return_value=False):
            result = await get_accounts()

    assert isinstance(result, list)