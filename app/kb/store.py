"""ChromaDB vector store wrapper for multimodal Knowledge Base chunks."""

from __future__ import annotations

from typing import Any

import chromadb
from chromadb.config import Settings

from ..config import CHROMA_DIR, ensure_runtime_dirs

COLLECTION_NAME = "knowledge"
_CLIENT: Any = None


def _get_collection():
    global _CLIENT
    ensure_runtime_dirs()
    if _CLIENT is None:
        _CLIENT = chromadb.PersistentClient(
            path=str(CHROMA_DIR),
            settings=Settings(anonymized_telemetry=False),
        )
    return _CLIENT.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )


def upsert_chunks(
    *,
    ids: list[str],
    embeddings: list[list[float]],
    documents: list[str],
    metadatas: list[dict[str, Any]],
) -> None:
    if not ids:
        return
    col = _get_collection()
    col.upsert(
        ids=ids,
        embeddings=embeddings,
        documents=documents,
        metadatas=metadatas,
    )


def delete_document_chunks(doc_id: int) -> None:
    col = _get_collection()
    try:
        col.delete(where={"doc_id": int(doc_id)})
    except Exception:
        pass


def query_chunks(
    *,
    query_embedding: list[float],
    allowed_doc_ids: list[int],
    top_k: int = 6,
    chunk_type: str | None = None,
) -> list[dict[str, Any]]:
    if not allowed_doc_ids or top_k <= 0:
        return []
    if chunk_type not in (None, "text", "image"):
        raise ValueError("chunk_type harus text atau image.")

    col = _get_collection()
    count = col.count()
    if not count:
        return []
    if len(allowed_doc_ids) == 1:
        where_filter: dict[str, Any] = {"doc_id": int(allowed_doc_ids[0])}
    else:
        where_filter = {"doc_id": {"$in": [int(i) for i in allowed_doc_ids]}}
    if chunk_type is not None:
        where_filter = {"$and": [where_filter, {"type": chunk_type}]}

    res = col.query(
        query_embeddings=[query_embedding],
        n_results=min(top_k, count),
        where=where_filter,
        include=["documents", "metadatas", "distances"],
    )

    docs_list = (res.get("documents") or [[]])[0]
    metas_list = (res.get("metadatas") or [[]])[0]
    dists_list = (res.get("distances") or [[]])[0]

    hits: list[dict[str, Any]] = []
    for doc_text, meta, dist in zip(docs_list, metas_list, dists_list):
        meta = meta or {}
        similarity = round(max(0.0, 1.0 - float(dist)), 4)
        item_type = meta.get("type", "text")
        hit: dict[str, Any] = {
            "doc_id": meta.get("doc_id"),
            "chunk_id": meta.get("chunk_index"),
            "filename": meta.get("filename", ""),
            "page": meta.get("page", 1),
            "type": item_type,
            "score": similarity,
        }
        if item_type == "image":
            hit["image_path"] = meta.get("image_path", "")
            hit["caption"] = doc_text
            hit["hint"] = (
                f"Panggil view_image('{meta.get('image_path', '')}') untuk melihat isi gambar/diagram ini secara visual."
            )
        else:
            hit["text"] = doc_text
        hits.append(hit)

    return hits


def get_chunks_by_indices(doc_id: int, chunk_indices: list[int]) -> list[dict[str, Any]]:
    """Fetch specific text chunks by their sequential chunk_index for a given doc_id."""
    if not chunk_indices:
        return []

    clean_indices = sorted({int(idx) for idx in chunk_indices if int(idx) >= 1})
    if not clean_indices:
        return []

    target_ids = [f"doc{int(doc_id)}_t_{idx}" for idx in clean_indices]
    col = _get_collection()
    res = col.get(
        ids=target_ids,
        include=["documents", "metadatas"],
    )

    docs_list = res.get("documents") or []
    metas_list = res.get("metadatas") or []

    chunks: list[dict[str, Any]] = []
    for doc_text, meta in zip(docs_list, metas_list):
        meta = meta or {}
        chunks.append(
            {
                "doc_id": meta.get("doc_id", int(doc_id)),
                "chunk_id": meta.get("chunk_index"),
                "filename": meta.get("filename", ""),
                "page": meta.get("page", 1),
                "type": meta.get("type", "text"),
                "text": doc_text,
            }
        )

    chunks.sort(key=lambda x: int(x.get("chunk_id") or 0))
    return chunks
