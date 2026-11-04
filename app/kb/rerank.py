"""OpenRouter text reranking, preserving original retrieval identities."""

from __future__ import annotations

import asyncio
import math
from typing import Any

import httpx

from ..config import (
    OPENROUTER_BASE,
    RERANK_MODEL,
    RERANK_TIMEOUT_SECONDS,
    get_openrouter_api_key,
)


def _map_results(payload: Any, candidates: list[dict[str, Any]], top_n: int) -> list[dict[str, Any]]:
    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, list) or len(results) != top_n:
        raise ValueError("Incomplete rerank results")
    seen: set[int] = set()
    mapped: list[dict[str, Any]] = []
    for result in results:
        if not isinstance(result, dict):
            raise ValueError("Invalid rerank result")
        index = result.get("index")
        score = result.get("relevance_score")
        if type(index) is not int or not 0 <= index < len(candidates) or index in seen:
            raise ValueError("Invalid rerank index")
        if type(score) not in (int, float) or not math.isfinite(score):
            raise ValueError("Invalid rerank score")
        seen.add(index)
        # The response index is a request position, NEVER a document chunk ID.
        mapped.append({**candidates[index], "rerank_score": float(score)})
    return sorted(mapped, key=lambda hit: hit["rerank_score"], reverse=True)


async def rerank_texts(
    query: str, candidates: list[dict[str, Any]], top_n: int
) -> list[dict[str, Any]]:
    """Score text candidates in one request; caller owns fallback and logging."""
    top_n = min(top_n, len(candidates))
    if top_n <= 0:
        return []
    if any(hit.get("type") != "text" for hit in candidates):
        raise ValueError("Reranker accepts only text chunks")
    if len(candidates) == 1:
        return candidates[:top_n]

    async def request() -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=RERANK_TIMEOUT_SECONDS) as client:
            response = await client.post(
                f"{OPENROUTER_BASE.rstrip('/')}/rerank",
                headers={"Authorization": f"Bearer {get_openrouter_api_key()}"},
                json={
                    "model": RERANK_MODEL,
                    "query": query,
                    "documents": [hit["text"] for hit in candidates],
                    "top_n": top_n,
                },
            )
            response.raise_for_status()
            return _map_results(response.json(), candidates, top_n)

    # A wall-clock bound also covers connection setup and slow streaming bodies.
    return await asyncio.wait_for(request(), timeout=RERANK_TIMEOUT_SECONDS)
