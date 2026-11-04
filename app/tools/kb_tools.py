"""ADK tools for listing and querying the multimodal Knowledge Base."""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from google.adk.tools.tool_context import ToolContext

from ..config import (
    RERANK_ENABLED,
    RERANK_FINAL_TOP_K,
    RERANK_IMAGE_TOP_K,
    RERANK_TEXT_CANDIDATES,
)
from ..kb.catalog import list_catalog_entries
from ..kb.embeddings import embed_query
from ..kb.ingest import wait_for_pending_ingest
from ..kb.rerank import rerank_texts
from ..kb.store import get_chunks_by_indices, query_chunks

logger = logging.getLogger(__name__)


def _parse_doc_ids(data: Any) -> set[int]:
    if data is None:
        return set()
    if isinstance(data, (int, float)):
        return {int(data)}
    if isinstance(data, str):
        return {int(m) for m in re.findall(r"\d+", data)}
    if isinstance(data, (list, tuple, set)):
        out: set[int] = set()
        for item in data:
            out.update(_parse_doc_ids(item))
        return out
    return set()


def get_knowledge_list(tool_context: ToolContext) -> dict[str, Any]:
    """List all available documents and shared datasets in the Knowledge Base.

    Returns each item's integer `id`, `filename`, `kind` ('document' or 'dataset'),
    `description`, `pages`, `image_count`, `status`, and workspace `path` (for CSV/Excel datasets).
    Call this tool before `search_knowledge_base` so you know which document IDs (`data=[id, ...]`)
    are relevant to filter on, or which shared CSV/Excel datasets exist in `shared/`.
    """
    session = getattr(tool_context, "session", None)
    session_id = getattr(session, "id", None) or "default"
    entries = [
        r
        for r in list_catalog_entries(session_id=session_id)
        if bool(r.get("enabled", 1))
    ]

    items: list[dict[str, Any]] = []
    for row in entries:
        item: dict[str, Any] = {
            "id": row["id"],
            "filename": row["filename"],
            "kind": row["kind"],
            "scope": row["scope"],
            "description": row["description"],
            "status": row["status"],
        }
        if row["kind"] == "dataset":
            item["workspace_path"] = f"shared/{row['filename']}"
            item["note"] = "Dataset tabular (CSV/Excel); olah menggunakan execute_python pada path workspace_path."
        else:
            item["pages"] = row["pages"]
            item["image_count"] = row["image_count"]
        items.append(item)

    return {
        "count": len(items),
        "items": items,
    }


async def search_knowledge_base(
    query: str,
    tool_context: ToolContext,
    data: list[int] | None = None,
) -> dict[str, Any]:
    """Perform semantic multimodal search (text chunks & embedded PDF diagrams/images) across the Knowledge Base.

    Use `data=[id1, id2]` to scope the search to specific document IDs obtained from `get_knowledge_list()`.
    If a returned result has `type='image'` and an `image_path`, call `view_image(image_path)` to inspect
    the diagram or visual content directly.
    Text hits preserve the original integer doc_id and chunk_id even after reranking.
    Use get_chunk(doc_id=hit.doc_id, chunk_ids=[hit.chunk_id - 1, hit.chunk_id + 1])
    to read neighboring text chunks. score is embedding similarity; rerank_score,
    when present, is a separate text relevance score, not comparable to image scores.

    Args:
        query: Natural-language search query describing the information, SOP, incident, or diagram needed.
        data: Optional list of integer document IDs to restrict search to (e.g., [1] or [1, 2]). If omitted, searches all ready documents visible to this session.
    """
    clean_query = (query or "").strip()
    if not clean_query:
        return {"error": "Query pencarian tidak boleh kosong."}

    session = getattr(tool_context, "session", None)
    session_id = getattr(session, "id", None) or "default"
    requested = _parse_doc_ids(data)
    await wait_for_pending_ingest(list(requested) if requested else None)

    visible_entries = list_catalog_entries(session_id=session_id)
    ready_doc_ids = [
        int(r["id"])
        for r in visible_entries
        if r["kind"] == "document"
        and r["status"] == "ready"
        and bool(r.get("enabled", 1))
    ]

    if not ready_doc_ids:
        return {
            "query": clean_query,
            "results": [],
            "rerank_status": "skipped" if RERANK_ENABLED else "disabled",
            "rerank_elapsed_ms": 0.0,
            "note": "Belum ada dokumen aktif berstatus 'ready' di Knowledge Base.",
        }

    if requested:
        target_ids = [doc_id for doc_id in ready_doc_ids if doc_id in requested]
        if not target_ids:
            return {
                "query": clean_query,
                "filtered_doc_ids": sorted(requested),
                "results": [],
                "rerank_status": "skipped" if RERANK_ENABLED else "disabled",
                "rerank_elapsed_ms": 0.0,
                "note": f"ID dokumen {sorted(requested)} tidak ditemukan atau belum berstatus 'ready'. ID dokumen yang tersedia: {ready_doc_ids}",
            }
    else:
        target_ids = ready_doc_ids

    try:
        q_vec = await embed_query(clean_query)
        status = "disabled"
        elapsed_ms = 0.0
        text_count = image_count = 0
        if not RERANK_ENABLED:
            hits = query_chunks(query_embedding=q_vec, allowed_doc_ids=target_ids, top_k=6)
            text_count = sum(hit["type"] == "text" for hit in hits)
            image_count = len(hits) - text_count
        else:
            texts = query_chunks(
                query_embedding=q_vec, allowed_doc_ids=target_ids,
                top_k=RERANK_TEXT_CANDIDATES, chunk_type="text",
            )
            images = query_chunks(
                query_embedding=q_vec, allowed_doc_ids=target_ids,
                top_k=min(RERANK_IMAGE_TOP_K, RERANK_FINAL_TOP_K), chunk_type="image",
            )
            text_count, image_count = len(texts), len(images)
            text_limit = min(len(texts), RERANK_FINAL_TOP_K - image_count)
            selected = texts[:text_limit]
            status = "skipped"
            if len(texts) > 1 and text_limit > 0:
                started = time.perf_counter()
                try:
                    selected = await rerank_texts(clean_query, texts, text_limit)
                    status = "applied"
                except Exception as exc:
                    status = "fallback"
                    # Log only the category: exception messages can contain request data.
                    logger.warning("KB rerank fallback error_category=%s", type(exc).__name__)
                finally:
                    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
            hits = selected + images
        logger.info(
            "KB rerank status=%s text_candidates=%d image_candidates=%d results=%d elapsed_ms=%.1f",
            status, text_count, image_count, len(hits), elapsed_ms,
        )
        return {
            "query": clean_query,
            "searched_doc_ids": target_ids,
            "results": hits,
            "rerank_status": status,
            "rerank_elapsed_ms": elapsed_ms,
        }
    except Exception as exc:
        return {"error": f"Gagal melakukan pencarian Knowledge Base: {exc}"}


async def get_chunk(
    doc_id: int,
    chunk_ids: list[int],
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Retrieve specific text chunks by their `chunk_id` from a document in the Knowledge Base.

    Use this tool when a chunk returned by `search_knowledge_base` is cut off mid-section,
    mid-table, or mid-list, and you want to read the neighboring chunks (e.g. `chunk_id - 1`,
    `chunk_id + 1`, `chunk_id + 2`).

    Args:
        doc_id: Integer ID of the document (e.g. 1 or 6).
        chunk_ids: List of integer `chunk_id` numbers to retrieve from that document (e.g. [18, 20, 21]).
    """
    target_doc_id = int(doc_id)
    indices = sorted(_parse_doc_ids(chunk_ids))
    if not indices:
        return {
            "error": "Parameter `chunk_ids` tidak boleh kosong.",
            "doc_id": target_doc_id,
        }

    await wait_for_pending_ingest([target_doc_id])

    session = getattr(tool_context, "session", None)
    session_id = getattr(session, "id", None) or "default"
    visible_entries = list_catalog_entries(session_id=session_id)
    allowed_ids = {
        int(r["id"])
        for r in visible_entries
        if r["kind"] == "document"
        and r["status"] == "ready"
        and bool(r.get("enabled", 1))
    }

    if target_doc_id not in allowed_ids:
        return {
            "error": f"Dokumen dengan id={target_doc_id} tidak ditemukan atau sedang dinonaktifkan.",
            "doc_id": target_doc_id,
        }

    chunks = get_chunks_by_indices(doc_id=target_doc_id, chunk_indices=indices)
    return {
        "doc_id": target_doc_id,
        "requested_chunk_ids": indices,
        "count": len(chunks),
        "chunks": chunks,
    }
