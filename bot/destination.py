"""
Fast LLM-based regional routing using OpenRouter API.
"""
import os
import json
import logging
import httpx

from bot.db import get_destinations

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Reuse HTTP client
_http_client = None


def get_http_client():
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(timeout=15.0)
    return _http_client


async def route_message(text: str, default_dest: int, cache) -> int:
    """
    Route message to regional destination based on content.
    Returns destination channel_id.
    """
    if not text:
        return default_dest


    try:
        # Get destinations
        dests = await get_destinations()
        if not dests:
            return default_dest

        dest_map = {d.name.lower(): d.channel_id for d in dests}
        regions = list(dest_map.keys())

        # Fast LLM call
        client = get_http_client()
        response = await client.post(
            OPENROUTER_URL,
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "anthropic/claude-3.5-sonnet",
                "messages": [{
                    "role": "user",
                    "content": f"""Classify this news into ONE region: {', '.join(regions)}

Regions:
- kaukasus: Armenia, Azerbaijan, Georgia
- südamerika: South America
- afrika: Africa  
- ukraine: Ukraine
- asien: Asia, China, India, Japan, Korea, Southeast Asia
- naher osten: Middle East, Syria, Iran, Turkey, Saudi Arabia, Israel

Text: {text[:1500]}

Reply ONLY with JSON: {{"region": "name", "confidence": 0.9}}"""
                }],
                "temperature": 0.1,
                "max_tokens": 50
            }
        )

        result = response.json()
        content = result["choices"][0]["message"]["content"].strip()

        # Parse JSON
        if "```" in content:
            content = content.split("```")[1].replace("json", "").strip()

        data = json.loads(content)
        region = data["region"].lower()
        confidence = data["confidence"]

        # Route if confident
        if confidence >= 0.6 and region in dest_map:
            channel_id = dest_map[region]
            logging.info(f"LLM Route → {region.upper()} (conf: {confidence:.2f})")
            return channel_id
        else:
            logging.info(f"Low confidence ({confidence:.2f}), using source default destination")

    except Exception as e:
        logging.error(f"Routing error: {e}")

    # Use source's configured destination
    return default_dest


async def get_destination(text: str, source_id: int, cache) -> int:
    """Get destination for message. Falls back to source's configured destination."""
    source = await cache.get_source(source_id)

    # Use source's configured destination as default/fallback
    source_default = source.destination

    if not source_default:
        logging.warning(f"No destination configured for source {source_id}")
        return None

    # Try LLM routing, falls back to source_default if confidence too low or error
    return await route_message(text, source_default, cache)