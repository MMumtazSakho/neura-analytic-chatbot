"""Adaptive upload routing for chat attachments and Knowledge Base files."""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

from .config import (
    KB_FILES_DIR,
    PDF_PAGE_LIMIT,
    SHARED_FILES_DIR,
    TEXT_CHAR_LIMIT,
    ensure_runtime_dirs,
)
from .kb.catalog import create_catalog_entry, update_catalog_entry
from .kb.extract import inspect_document_size
from .kb.ingest import ingest_document_file, schedule_document_ingest
from .sandbox.kernel_pool import ensure_session_workspace

SPREADSHEET_EXTENSIONS = {".csv", ".xlsx", ".xls", ".tsv"}
DOCUMENT_EXTENSIONS = {".pdf", ".docx", ".txt", ".md", ".log"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


def sanitize_filename(filename: str) -> str:
    base = Path(filename or "uploaded_file").name
    clean = re.sub(r"[^a-zA-Z0-9._\- ()]", "_", base).strip()
    return clean or "uploaded_file"


async def handle_chat_upload(
    *,
    session_id: str,
    filename: str,
    file_bytes: bytes,
) -> dict[str, Any]:
    """Route a file uploaded via the chat attachment button according to type and size."""
    ensure_runtime_dirs()
    safe_name = sanitize_filename(filename)
    suffix = Path(safe_name).suffix.lower()

    ws = ensure_session_workspace(session_id)
    upload_dest = ws / "uploads" / safe_name
    upload_dest.write_bytes(file_bytes)

    # 1. Spreadsheets always go directly to the session Python workspace
    if suffix in SPREADSHEET_EXTENSIONS:
        return {
            "filename": safe_name,
            "route": "workspace_spreadsheet",
            "workspace_path": f"uploads/{safe_name}",
            "system_note": (
                f"[Lampiran Chat: '{safe_name}' (spreadsheet) telah disimpan di workspace Python pada path "
                f"`uploads/{safe_name}`. Gunakan tool `execute_python` (pandas) untuk membaca dan menganalisisnya.]"
            ),
        }

    # 2. Images go to workspace uploads/ so agent can view_image or process them
    if suffix in IMAGE_EXTENSIONS:
        return {
            "filename": safe_name,
            "route": "workspace_image",
            "workspace_path": f"uploads/{safe_name}",
            "system_note": (
                f"[Lampiran Chat: Gambar '{safe_name}' disimpan di `uploads/{safe_name}`. "
                f"Gunakan tool `view_image('uploads/{safe_name}')` untuk melihat isinya secara visual.]"
            ),
        }

    # 3. Documents (PDF, DOCX, TXT): inspect size threshold
    if suffix in DOCUMENT_EXTENSIONS:
        pages, chars = inspect_document_size(upload_dest)
        is_small_pdf = suffix == ".pdf" and pages <= PDF_PAGE_LIMIT
        is_small_text = suffix != ".pdf" and chars <= TEXT_CHAR_LIMIT

        if is_small_pdf or is_small_text:
            size_label = f"{pages} halaman" if suffix == ".pdf" else f"{chars:,} karakter"
            return {
                "filename": safe_name,
                "route": "workspace_document",
                "pages": pages,
                "chars": chars,
                "workspace_path": f"uploads/{safe_name}",
                "system_note": (
                    f"[Lampiran Chat: Dokumen pendek '{safe_name}' ({size_label}) disimpan di workspace pada path "
                    f"`uploads/{safe_name}`. Karena ukurannya kecil, gunakan tool `execute_python` "
                    f"(mis. `fitz` untuk PDF, `docx` untuk DOCX, atau `open()` untuk TXT) untuk membaca isinya secara langsung.]"
                ),
            }

        # Large document via chat -> index into ChromaDB with session scope
        kb_dest_Placeholder = KB_FILES_DIR / f"session_{session_id}_{safe_name}"
        shutil.copy2(upload_dest, kb_dest_Placeholder)

        doc_id = create_catalog_entry(
            filename=safe_name,
            kind="document",
            scope="session",
            session_id=session_id,
            description=f"Diunggah melalui chat ({pages} hal, {chars:,} karakter)",
            pages=pages,
            chars=chars,
            status="indexing",
            stored_path=str(kb_dest_Placeholder),
        )
        schedule_document_ingest(
            doc_id=doc_id, file_path=kb_dest_Placeholder, filename=safe_name
        )

        size_label = f"{pages} halaman" if suffix == ".pdf" else f"{chars:,} karakter"
        return {
            "filename": safe_name,
            "route": "knowledge_base_session",
            "doc_id": doc_id,
            "pages": pages,
            "chars": chars,
            "status": "indexing",
            "workspace_path": f"uploads/{safe_name}",
            "system_note": (
                f"[Lampiran Chat: Dokumen besar '{safe_name}' ({size_label}) sedang di-index ke Knowledge Base "
                f"dengan `id={doc_id}`. Gunakan `search_knowledge_base(query=..., data=[{doc_id}])` untuk mencari informasi di dalamnya.]"
            ),
        }

    # Fallback: treat as workspace file
    return {
        "filename": safe_name,
        "route": "workspace_file",
        "workspace_path": f"uploads/{safe_name}",
        "system_note": (
            f"[Lampiran Chat: File '{safe_name}' tersedia di workspace pada path `uploads/{safe_name}`.]"
        ),
    }


async def handle_kb_upload(
    *,
    filename: str,
    file_bytes: bytes,
    description: str | None = None,
    background: bool = True,
) -> dict[str, Any]:
    """Handle an upload via the Knowledge Base panel.

    - CSV/XLSX files skip vector indexing and are placed directly in `shared_files/`
      (accessible in every session's Python workspace as `shared/<filename>`).
    - PDF/DOCX/TXT files are indexed into ChromaDB with `scope='global'`.
    """
    ensure_runtime_dirs()
    safe_name = sanitize_filename(filename)
    suffix = Path(safe_name).suffix.lower()

    if suffix in SPREADSHEET_EXTENSIONS:
        dest = SHARED_FILES_DIR / safe_name
        dest.write_bytes(file_bytes)
        doc_id = create_catalog_entry(
            filename=safe_name,
            kind="dataset",
            scope="global",
            description=description or "Dataset CSV/Excel di workspace shared/",
            pages=0,
            chars=len(file_bytes),
            status="ready",
            stored_path=str(dest),
        )
        return {
            "id": doc_id,
            "filename": safe_name,
            "kind": "dataset",
            "scope": "global",
            "status": "ready",
            "workspace_path": f"shared/{safe_name}",
        }

    if suffix not in DOCUMENT_EXTENSIONS:
        raise ValueError(
            f"Format file '{suffix}' tidak didukung di Knowledge Base. Gunakan PDF, DOCX, TXT, CSV, atau XLSX."
        )

    temp_path = KB_FILES_DIR / safe_name
    temp_path.write_bytes(file_bytes)
    pages, chars = inspect_document_size(temp_path)

    doc_id = create_catalog_entry(
        filename=safe_name,
        kind="document",
        scope="global",
        description=description,
        pages=pages,
        chars=chars,
        status="indexing",
        stored_path=str(temp_path),
    )

    final_path = KB_FILES_DIR / f"doc{doc_id}_{safe_name}"
    temp_path.replace(final_path)
    update_catalog_entry(doc_id, stored_path=str(final_path))

    if background:
        schedule_document_ingest(doc_id=doc_id, file_path=final_path, filename=safe_name)
        status = "indexing"
    else:
        await ingest_document_file(doc_id=doc_id, file_path=final_path, filename=safe_name)
        status = "ready"

    return {
        "id": doc_id,
        "filename": safe_name,
        "kind": "document",
        "scope": "global",
        "pages": pages,
        "chars": chars,
        "status": status,
    }
