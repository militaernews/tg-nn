"""
Fast LLM-based regional routing using OpenRouter API.
Optimized with connection pooling and pre-computed mappings.
"""
import json
import logging
import os

from httpx import AsyncClient, Limits

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Reuse HTTP client with connection pooling
_http_client = None


def get_http_client():
    """Get or create HTTP client with optimized connection pooling."""
    global _http_client
    if _http_client is None:
        _http_client = AsyncClient(
            timeout=15.0,
            limits=Limits(
                max_connections=100,
                max_keepalive_connections=20,
                keepalive_expiry=30.0
            )
        )
    return _http_client


async def route_message(text: str, default_dest: int, cache) -> int:
    """
    Route message to regional destination based on content.
    Returns destination channel_id.
    Optimized with pre-computed mappings and minimal async overhead.
    """
    if not text:
        return default_dest

    try:
        # Get pre-computed destination mappings (synchronous, no await!)
        dest_map = cache.get_destination_map()
        if not dest_map:
            logging.warning("No destinations in cache, using default")
            return default_dest

        # Get pre-computed regions list (synchronous, no await!)
        regions = cache.get_destination_regions()

        # Fast LLM call with optimized HTTP client
        client = get_http_client()
        response = await client.post(
            OPENROUTER_URL,
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "allenai/olmo-3.1-32b-think:free",
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

        # Optimized JSON parsing
        if "```" in content:
            # Extract content between backticks efficiently
            start = content.find("```")
            end = content.rfind("```")
            if start != -1 and end != -1 and end > start:
                content = content[start+3:end].replace("json", "", 1).strip()

        data = json.loads(content)
        region = data["region"].lower()
        confidence = data["confidence"]

        # Route if confident - single O(1) dict lookup
        if confidence >= 0.6:
            channel_id = dest_map.get(region)
            if channel_id:
                logging.info(f"LLM Route → {region.upper()} (conf: {confidence:.2f})")
                return channel_id
            else:
                logging.warning(f"Unknown region '{region}' from LLM, using default")
        else:
            logging.info(f"Low confidence ({confidence:.2f}), using source default")

    except json.JSONDecodeError as e:
        logging.error(f"JSON parsing error: {e}, content: {content[:200]}")
    except KeyError as e:
        logging.error(f"Missing key in LLM response: {e}")
    except Exception as e:
        logging.error(f"Routing error: {e}", exc_info=True)

    # Use source's configured destination
    return default_dest


async def get_destination(text: str, source_id: int, cache) -> int:
    """Get destination for message. Falls back to source's configured destination."""
    # Get source from cache (single O(1) dict lookup)
    source = await cache.get_source(source_id)

    # Use source's configured destination as default/fallback
    source_default = source.destination

    if not source_default:
        logging.warning(f"No destination configured for source {source_id}")
        return None

    # Try LLM routing, falls back to source_default if confidence too low or error
    return await route_message(text, source_default, cache)