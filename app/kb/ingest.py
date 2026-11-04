"""Chunking and multimodal ingestion pipeline into ChromaDB."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from ..config import KB_IMAGES_DIR
from .catalog import delete_catalog_entry, get_catalog_entry, update_catalog_entry
from .embeddings import embed_image, embed_texts
from .extract import extract_document
from .store import delete_document_chunks, upsert_chunks

logger = logging.getLogger(__name__)

CHUNK_SIZE = 1200
CHUNK_OVERLAP = 200

_ACTIVE_INGEST_TASKS: dict[int, asyncio.Task[None]] = {}


def chunk_page_text(text: str, max_chars: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split text into overlapping chunks breaking preferentially at paragraph or sentence boundaries."""
    clean = text.strip()
    if not clean:
        return []
    if len(clean) <= max_chars:
        return [clean]

    chunks: list[str] = []
    start = 0
    length = len(clean)

    while start < length:
        end = min(start + max_chars, length)
        if end < length:
            # Look for paragraph break or sentence break in the last 30% of the window
            window_start = start + int(max_chars * 0.65)
            para_break = clean.rfind("\n\n", window_start, end)
            line_break = clean.rfind("\n", window_start, end)
            sent_break = clean.rfind(". ", window_start, end)

            if para_break != -1:
                end = para_break
            elif line_break != -1:
                end = line_break
            elif sent_break != -1:
                end = sent_break + 1

        piece = clean[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= length:
            break
        start = max(start + 1, end - overlap)

    return chunks


async def ingest_document_file(doc_id: int, file_path: Path, filename: str) -> None:
    """Extract, chunk, embed (text + images), and store a document in ChromaDB."""
    try:
        extracted = extract_document(file_path, doc_id=doc_id)
        update_catalog_entry(
            doc_id,
            pages=extracted.total_pages,
            chars=extracted.total_chars,
            image_count=len(extracted.images),
        )

        chunk_ids: list[str] = []
        chunk_texts: list[str] = []
        chunk_metas: list[dict] = []

        idx_counter = 0
        for page_obj in extracted.pages:
            pieces = chunk_page_text(page_obj.text)
            for piece in pieces:
                idx_counter += 1
                chunk_ids.append(f"doc{doc_id}_t_{idx_counter}")
                chunk_texts.append(piece)
                chunk_metas.append(
                    {
                        "doc_id": int(doc_id),
                        "filename": filename,
                        "page": int(page_obj.page),
                        "type": "text",
                        "chunk_index": idx_counter,
                    }
                )

        if chunk_texts:
            text_embeddings = await embed_texts(chunk_texts)
            upsert_chunks(
                ids=chunk_ids,
                embeddings=text_embeddings,
                documents=chunk_texts,
                metadatas=chunk_metas,
            )

        # Embed extracted images into the same vector space
        img_ids: list[str] = []
        img_embeddings: list[list[float]] = []
        img_docs: list[str] = []
        img_metas: list[dict] = []

        for img_idx, img_obj in enumerate(extracted.images, start=1):
            caption = (
                f"Gambar/diagram dari {filename} halaman {img_obj.page}. "
                f"Konteks halaman: {img_obj.context_text}"
            ).strip()
            vec = await embed_image(img_obj.abs_path, context_hint=caption)
            img_ids.append(f"doc{doc_id}_i_{img_idx}")
            img_embeddings.append(vec)
            img_docs.append(caption)
            img_metas.append(
                {
                    "doc_id": int(doc_id),
                    "filename": filename,
                    "page": int(img_obj.page),
                    "type": "image",
                    "image_path": img_obj.image_path,
                    "chunk_index": img_idx,
                }
            )

        if img_ids:
            upsert_chunks(
                ids=img_ids,
                embeddings=img_embeddings,
                documents=img_docs,
                metadatas=img_metas,
            )

        update_catalog_entry(doc_id, status="ready")
    except Exception as exc:
        logger.exception("Gagal meng-index dokumen id=%s (%s)", doc_id, filename)
        update_catalog_entry(doc_id, status="failed", error=str(exc))


def schedule_document_ingest(doc_id: int, file_path: Path, filename: str) -> asyncio.Task[None]:
    """Launch document ingestion in the background and track the task by doc_id."""
    task = asyncio.create_task(
        ingest_document_file(doc_id=doc_id, file_path=file_path, filename=filename)
    )
    _ACTIVE_INGEST_TASKS[doc_id] = task

    def _cleanup(_t: asyncio.Task[None]) -> None:
        _ACTIVE_INGEST_TASKS.pop(doc_id, None)

    task.add_done_callback(_cleanup)
    return task


async def wait_for_pending_ingest(doc_ids: list[int] | None = None) -> None:
    """Wait for any in-flight background indexing tasks before querying the KB."""
    if doc_ids:
        tasks = [_ACTIVE_INGEST_TASKS[i] for i in doc_ids if i in _ACTIVE_INGEST_TASKS]
    else:
        tasks = list(_ACTIVE_INGEST_TASKS.values())
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def remove_document_completely(doc_id: int) -> bool:
    """Delete a catalog entry, its ChromaDB vectors, stored file, and extracted images."""
    entry = get_catalog_entry(doc_id)
    if not entry:
        return False

    delete_document_chunks(doc_id)

    stored_path = entry.get("stored_path")
    target_paths: list[Path] = []
    if stored_path:
        target_paths.append(Path(stored_path))
    # Fallbacks if absolute path differs between local macOS and container
    if entry.get("filename"):
        from ..config import KB_FILES_DIR, SHARED_FILES_DIR
        target_paths.append(KB_FILES_DIR / entry["filename"])
        target_paths.append(SHARED_FILES_DIR / entry["filename"])

    for p in target_paths:
        if p.is_file():
            try:
                p.unlink()
            except OSError:
                pass

    for img_file in KB_IMAGES_DIR.glob(f"doc{doc_id}_*"):
        try:
            img_file.unlink()
        except OSError:
            pass

    delete_catalog_entry(doc_id)
    return True
