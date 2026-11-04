"""SQLite catalog for Knowledge Base documents and shared datasets."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import APP_STATE_DB_PATH, KB_FILES_DIR, SHARED_FILES_DIR, ensure_runtime_dirs


def _conn() -> sqlite3.Connection:
    ensure_runtime_dirs()
    conn = sqlite3.connect(str(APP_STATE_DB_PATH), timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS kb_catalog (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT 'document',
            scope TEXT NOT NULL DEFAULT 'global',
            session_id TEXT,
            description TEXT,
            pages INTEGER DEFAULT 0,
            chars INTEGER DEFAULT 0,
            image_count INTEGER DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'indexing',
            enabled INTEGER NOT NULL DEFAULT 1,
            error TEXT,
            stored_path TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    cols = {
        r["name"]
        for r in conn.execute("PRAGMA table_info(kb_catalog)").fetchall()
    }
    if "enabled" not in cols:
        conn.execute(
            "ALTER TABLE kb_catalog ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1"
        )
    conn.commit()
    return conn


def create_catalog_entry(
    *,
    filename: str,
    kind: str = "document",
    scope: str = "global",
    session_id: str | None = None,
    description: str | None = None,
    pages: int = 0,
    chars: int = 0,
    status: str = "indexing",
    stored_path: str | None = None,
) -> int:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with _conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO kb_catalog
                (filename, kind, scope, session_id, description, pages, chars, status, stored_path, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                filename,
                kind,
                scope,
                session_id,
                (description or "").strip() or None,
                pages,
                chars,
                status,
                stored_path,
                now,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


def update_catalog_entry(
    doc_id: int,
    *,
    status: str | None = None,
    pages: int | None = None,
    chars: int | None = None,
    image_count: int | None = None,
    description: str | None = None,
    error: str | None = None,
    stored_path: str | None = None,
) -> None:
    fields: list[str] = []
    values: list[Any] = []
    if status is not None:
        fields.append("status = ?")
        values.append(status)
    if pages is not None:
        fields.append("pages = ?")
        values.append(pages)
    if chars is not None:
        fields.append("chars = ?")
        values.append(chars)
    if image_count is not None:
        fields.append("image_count = ?")
        values.append(image_count)
    if description is not None:
        fields.append("description = ?")
        values.append(description)
    if error is not None:
        fields.append("error = ?")
        values.append(error)
    if stored_path is not None:
        fields.append("stored_path = ?")
        values.append(stored_path)

    if not fields:
        return
    values.append(doc_id)
    with _conn() as conn:
        conn.execute(
            f"UPDATE kb_catalog SET {', '.join(fields)} WHERE id = ?",
            values,
        )
        conn.commit()


def list_catalog_entries(session_id: str | None = None, include_all_sessions: bool = False) -> list[dict[str, Any]]:
    with _conn() as conn:
        if include_all_sessions:
            rows = conn.execute(
                "SELECT * FROM kb_catalog ORDER BY id ASC"
            ).fetchall()
        elif session_id:
            rows = conn.execute(
                "SELECT * FROM kb_catalog WHERE scope = 'global' OR (scope = 'session' AND session_id = ?) ORDER BY id ASC",
                (session_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM kb_catalog WHERE scope = 'global' ORDER BY id ASC"
            ).fetchall()
    return [dict(r) for r in rows]


def get_catalog_entry(doc_id: int) -> dict[str, Any] | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM kb_catalog WHERE id = ?", (doc_id,)
        ).fetchone()
    return dict(row) if row else None


def delete_catalog_entry(doc_id: int) -> dict[str, Any] | None:
    entry = get_catalog_entry(doc_id)
    if not entry:
        return None
    with _conn() as conn:
        conn.execute("DELETE FROM kb_catalog WHERE id = ?", (doc_id,))
        conn.commit()
    return entry


def set_catalog_entry_enabled(
    doc_id: int, enabled: bool | None = None
) -> dict[str, Any] | None:
    """Toggle or explicitly set the enabled state (1 or 0) of a Knowledge Base entry."""
    entry = get_catalog_entry(doc_id)
    if not entry:
        return None

    current_bool = bool(entry.get("enabled", 1))
    new_bool = (not current_bool) if enabled is None else bool(enabled)
    new_val = 1 if new_bool else 0

    stored_path = entry.get("stored_path")
    if entry.get("kind") == "dataset" and entry.get("filename"):
        shared_file = SHARED_FILES_DIR / entry["filename"]
        disabled_file = KB_FILES_DIR / f"disabled_ds_{doc_id}_{entry['filename']}"
        try:
            if not new_bool and shared_file.is_file():
                shared_file.replace(disabled_file)
                stored_path = str(disabled_file)
            elif new_bool and disabled_file.is_file():
                disabled_file.replace(shared_file)
                stored_path = str(shared_file)
        except OSError:
            pass

    with _conn() as conn:
        conn.execute(
            "UPDATE kb_catalog SET enabled = ?, stored_path = ? WHERE id = ?",
            (new_val, stored_path, doc_id),
        )
        conn.commit()
    return get_catalog_entry(doc_id)


def set_all_catalog_enabled(enabled: bool, session_id: str | None = None) -> list[dict[str, Any]]:
    """Enable or disable all visible Knowledge Base entries at once."""
    entries = list_catalog_entries(session_id=session_id)
    for item in entries:
        set_catalog_entry_enabled(int(item["id"]), enabled=enabled)
    return list_catalog_entries(session_id=session_id)
