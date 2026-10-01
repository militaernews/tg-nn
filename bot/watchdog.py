import asyncio
import logging
import os

from pyrogram import Client

HEARTBEAT_INTERVAL = 300  # seconds between probes
HEARTBEAT_TIMEOUT = 60  # seconds to wait for a response before giving up


async def heartbeat(client: Client, interval: int = HEARTBEAT_INTERVAL,
                     timeout: int = HEARTBEAT_TIMEOUT) -> None:
    """Periodically probe the Telegram connection and hard-exit the process
    if it stops responding.

    Pyrogram's internal update dispatcher can wedge after a run of RPC
    timeouts without the client or process ever crashing (tg-nn-processor
    did this on 2026-09-22: stayed "running" but silently stopped
    dispatching updates for over a week), so systemd's Restart=always never
    kicks in. os._exit forces an immediate restart instead of relying on
    the client to recover on its own.
    """
    while True:
        await asyncio.sleep(interval)
        try:
            await asyncio.wait_for(client.get_me(), timeout=timeout)
        except Exception as e:
            logging.critical(f"Heartbeat failed ({e}), exiting for restart")
            os._exit(1)
