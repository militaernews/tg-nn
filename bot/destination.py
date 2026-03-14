"""
Fast LLM-based regional routing using OpenRouter API.
Optimized with connection pooling, pre-computed mappings, and fallback models.
"""
import json
import logging
import os
import asyncio
from typing import Optional, Dict, List, Any

from httpx import AsyncClient, Limits, HTTPStatusError

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Models to try in order of preference (cost/reliability)
# 1. Google Gemini 2.0 Flash Lite (Very low cost, high speed)
# 2. Google Gemini 2.0 Flash (Reliable, fast)
# 3. Meta Llama 3.3 70B (Fallback)
MODELS = [
    "google/gemini-2.0-flash-lite-001",
    "google/gemini-2.0-flash-001",
    "meta-llama/llama-3.3-70b-instruct"
]

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


async def call_llm(model: str, prompt: str) -> Optional[str]:
    """Make a single LLM call with error handling."""
    client = get_http_client()
    try:
        response = await client.post(
            OPENROUTER_URL,
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/militaernews/tg-nn",
                "X-Title": "TG-NN Content Router"
            },
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
                "max_tokens": 100,
                "response_format": {"type": "json_object"}
            }
        )
        response.raise_for_status()
        result = response.json()
        return result["choices"][0]["message"]["content"].strip()
    except HTTPStatusError as e:
        logging.warning(f"LLM call failed for model {model}: {e.response.status_code}")
    except Exception as e:
        logging.error(f"LLM call error for model {model}: {e}")
    return None


async def route_message(text: str, default_dest: int, cache) -> int:
    """
    Route message to regional destination based on content.
    Returns destination channel_id.
    """
    if not text or not OPENROUTER_API_KEY:
        return default_dest

    try:
        dest_map = cache.get_destination_map()
        if not dest_map:
            logging.warning("No destinations in cache, using default")
            return default_dest

        regions = cache.get_destination_regions()
        
        # Construct a clear prompt for classification
        prompt = f"""Classify the following news post into exactly ONE of these regions: {', '.join(regions)}.
        
Context for regions:
- kaukasus: Armenia, Azerbaijan, Georgia
- südamerika: South America
- afrika: Africa  
- ukraine: Ukraine, Russia-Ukraine war
- asien: Asia, China, India, Japan, Korea, Southeast Asia
- naher osten: Middle East, Syria, Iran, Turkey, Saudi Arabia, Israel, Palestine

Text: {text[:2000]}

Return ONLY a JSON object: {{"region": "region_name", "confidence": 0.0-1.0}}"""

        content = None
        for model in MODELS:
            content = await call_llm(model, prompt)
            if content:
                break
        
        if not content:
            logging.error("All LLM models failed to respond")
            return default_dest

        # Clean and parse JSON
        try:
            # Handle potential markdown wrapping
            if "```" in content:
                start = content.find("{")
                end = content.rfind("}") + 1
                if start != -1 and end != -1:
                    content = content[start:end]
            
            data = json.loads(content)
            region = data.get("region", "").lower().strip()
            confidence = data.get("confidence", 0.0)

            if confidence >= 0.5 and region in dest_map:
                channel_id = dest_map[region]
                logging.info(f"LLM Route → {region.upper()} (conf: {confidence:.2f})")
                return channel_id
            else:
                logging.info(f"Low confidence ({confidence:.2f}) or unknown region '{region}', using default")

        except (json.JSONDecodeError, KeyError) as e:
            logging.error(f"JSON parsing error: {e}, content: {content[:200]}")

    except Exception as e:
        logging.error(f"Routing error: {e}", exc_info=True)

    return default_dest


async def get_destination(text: str, source_id: int, cache) -> Optional[int]:
    """Get destination for message. Falls back to source's configured destination."""
    source = await cache.get_source(source_id)
    if not source:
        logging.warning(f"Source {source_id} not found")
        return None

    source_default = source.destination
    if not source_default:
        logging.warning(f"No destination configured for source {source_id}")
        # If no default is set, we still try to route, but might return None
        # However, the current logic expects a default. 
        # Let's use a fallback if possible or return None.
        pass

    destination = await route_message(text, source_default, cache)
    logging.info(f"Routing: Source {source_id} -> Destination {destination}")
    return destination
