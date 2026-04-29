"""
Fast LLM-based regional routing using FREE OpenRouter models.
Optimized with connection pooling, pre-computed mappings, and fallback models.
"""
import json
import logging
import os
import asyncio
from typing import Optional

from httpx import AsyncClient, Limits, HTTPStatusError

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# FREE Models to try in order of preference
FREE_MODELS = [
    "google/gemini-2.0-flash-lite-preview-02-05:free",
    "google/gemini-2.0-flash-exp:free",
    "meta-llama/llama-3.1-8b-instruct:free",
    "mistralai/mistral-7b-instruct:free",
]

_HEADERS = {
    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
    "Content-Type": "application/json",
    "HTTP-Referer": "https://github.com/militaernews/tg-nn",
    "X-Title": "TG-NN Content Router",
}

# Single shared client — created once, never closed (lives with process)
# keepalive_expiry must be shorter than Neon's idle timeout so the HTTP
# connection doesn't hold the compute awake between messages.
_http_client: Optional[AsyncClient] = None


def get_http_client() -> AsyncClient:
    global _http_client
    if _http_client is None:
        _http_client = AsyncClient(
            timeout=15.0,
            limits=Limits(
                max_connections=10,          # LLM calls are rare — no need for 100
                max_keepalive_connections=2,
                keepalive_expiry=25.0,       # < Neon's 30s idle so TCP closes cleanly
            ),
        )
    return _http_client


def _build_prompt(text: str, regions: list[str]) -> str:
    # Truncate early — LLMs don't need more than ~500 chars to classify region
    snippet = text[:500].replace("\n", " ").strip()
    return (
        f"Classify this news snippet into exactly ONE region: {', '.join(regions)}.\n\n"
        "Regions:\n"
        "- kaukasus: Armenia, Azerbaijan, Georgia\n"
        "- südamerika: South America\n"
        "- afrika: Africa\n"
        "- ukraine: Ukraine, Russia-Ukraine war\n"
        "- asien: Asia (China, India, Japan, Korea, SE Asia)\n"
        "- naher osten: Middle East (Syria, Iran, Turkey, Saudi Arabia, Israel, Palestine)\n\n"
        f'Text: "{snippet}"\n\n'
        'Return ONLY JSON: {"region": "region_name", "confidence": 0.0-1.0}'
    )


async def _call_llm(model: str, prompt: str) -> Optional[str]:
    """Single LLM call. Returns raw content string or None on failure."""
    try:
        response = await get_http_client().post(
            OPENROUTER_URL,
            headers=_HEADERS,
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
                "max_tokens": 60,           # region + confidence fits in <60 tokens
                "response_format": {"type": "json_object"},
            },
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"].strip()
    except HTTPStatusError as e:
        logging.warning(f"LLM {model} → HTTP {e.response.status_code}")
    except Exception as e:
        logging.error(f"LLM {model} → {e}")
    return None


def _parse_region(content: str) -> tuple[str, float]:
    """Extract (region, confidence) from LLM JSON response."""
    # Strip markdown fences if present
    if "```" in content:
        start, end = content.find("{"), content.rfind("}") + 1
        if start != -1 and end:
            content = content[start:end]
    data = json.loads(content)
    return data.get("region", "").lower().strip(), float(data.get("confidence", 0.0))


async def route_message(text: str, default_dest: int, cache) -> int:
    """
    Route message to regional destination based on content.
    Tries FREE_MODELS in order, returns first confident match.
    Falls back to default_dest on any failure.
    """
    if not text or not OPENROUTER_API_KEY:
        return default_dest

    dest_map = cache.get_destination_map()
    if not dest_map:
        logging.warning("No destinations in cache, using default")
        return default_dest

    regions = cache.get_destination_regions()
    prompt = _build_prompt(text, regions)

    for model in FREE_MODELS:
        content = await _call_llm(model, prompt)
        if not content:
            continue

        try:
            region, confidence = _parse_region(content)
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logging.error(f"JSON parse error from {model}: {e} — raw: {content[:100]}")
            continue

        if confidence >= 0.5 and region in dest_map:
            logging.info(f"Routed → {region.upper()} (conf={confidence:.2f}, model={model})")
            return dest_map[region]

        logging.info(f"Low confidence ({confidence:.2f}) or unknown region '{region}' from {model}")
        # Don't try next model for low-confidence — the text is probably ambiguous
        break

    return default_dest


async def get_destination(text: str, source_id: int, cache) -> Optional[int]:
    """Get destination channel_id for a message. Returns None if source is unknown."""
    source = await cache.get_source(source_id)
    if not source:
        logging.warning(f"Source {source_id} not found in cache")
        return None

    if not source.destination:
        logging.warning(f"No default destination for source {source_id}")
        return None

    destination = await route_message(text, source.destination, cache)
    logging.info(f"Source {source_id} → destination {destination}")
    return destination