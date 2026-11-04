"""OpenRouter multimodal embedding client using google/gemini-embedding-2."""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from typing import Any

import httpx

from ..config import EMBED_DIM, EMBED_MODEL, OPENROUTER_BASE, get_openrouter_api_key

BATCH_SIZE = 20


async def _post_embeddings(payload: dict[str, Any]) -> dict[str, Any]:
    api_key = get_openrouter_api_key()
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    url = f"{OPENROUTER_BASE.rstrip('/')}/embeddings"

    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            last_exc = exc
            if attempt < 2:
                await asyncio.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Gagal memanggil OpenRouter embeddings ({EMBED_MODEL}): {last_exc}")


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a list of text strings into the unified vector space."""
    if not texts:
        return []

    all_vectors: list[list[float]] = []
    for start in range(0, len(texts), BATCH_SIZE):
        batch = texts[start : start + BATCH_SIZE]
        payload = {
            "model": EMBED_MODEL,
            "dimensions": EMBED_DIM,
            "input": batch,
        }
        data = await _post_embeddings(payload)
        items = sorted(data.get("data", []), key=lambda x: x.get("index", 0))
        for item in items:
            all_vectors.append(item["embedding"])
    return all_vectors


async def embed_query(query: str) -> list[float]:
    """Embed a single search query."""
    vecs = await embed_texts([query])
    if not vecs:
        raise RuntimeError("Embedding query kosong.")
    return vecs[0]


async def embed_image(image_path: Path, context_hint: str = "") -> list[float]:
    """Embed an image (and optional brief context caption) into the same unified vector space."""
    raw_bytes = image_path.read_bytes()
    b64 = base64.b64encode(raw_bytes).decode("ascii")
    data_uri = f"data:image/png;base64,{b64}"

    content_parts: list[dict[str, Any]] = []
    if context_hint.strip():
        content_parts.append({"type": "text", "text": context_hint.strip()[:500]})
    content_parts.append(
        {
            "type": "image_url",
            "image_url": {"url": data_uri},
        }
    )

    payload = {
        "model": EMBED_MODEL,
        "dimensions": EMBED_DIM,
        "input": [{"content": content_parts}],
    }
    data = await _post_embeddings(payload)
    items = data.get("data", [])
    if not items:
        raise RuntimeError(f"Tidak ada embedding yang dikembalikan untuk gambar {image_path.name}")
    return items[0]["embedding"]
