"""Native image viewing tool for inspecting generated plots and extracted PDF visuals."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from google.adk.tools.tool_context import ToolContext
from PIL import Image

from ..callbacks import queue_image_for_session
from ..config import KB_IMAGES_DIR, SHARED_FILES_DIR, WORKSPACES_DIR

MAX_IMAGE_DIMENSION = 1568


def _resolve_image_path(session_id: str, raw_path: str) -> tuple[Path | None, str]:
    """Resolve a workspace-relative or kb_images-relative image path safely."""
    clean = raw_path.strip().lstrip("/")
    if clean.startswith(f"/api/sessions/{session_id}/files/"):
        clean = clean[len(f"/api/sessions/{session_id}/files/") :]
    elif clean.startswith("/api/kb/images/"):
        clean = "kb_images/" + clean[len("/api/kb/images/") :]

    workspace_root = (WORKSPACES_DIR / session_id).resolve()
    workspace_root.mkdir(parents=True, exist_ok=True)
    kb_images_root = KB_IMAGES_DIR.resolve()
    shared_root = SHARED_FILES_DIR.resolve()

    candidates: list[tuple[Path, Path, str]] = []
    if clean.startswith("kb_images/"):
        sub = clean[len("kb_images/") :]
        candidates.append((kb_images_root / sub, kb_images_root, f"kb_images/{sub}"))
    elif clean.startswith("shared/"):
        sub = clean[len("shared/") :]
        candidates.append((shared_root / sub, shared_root, f"shared/{sub}"))
    else:
        candidates.append((workspace_root / clean, workspace_root, clean))
        # Also allow bare filename inside outputs/ or kb_images/
        candidates.append((workspace_root / "outputs" / clean, workspace_root, f"outputs/{clean}"))
        candidates.append((kb_images_root / clean, kb_images_root, f"kb_images/{clean}"))

    for candidate, allowed_root, canonical_rel in candidates:
        try:
            resolved = candidate.resolve()
            resolved.relative_to(allowed_root)
        except ValueError:
            continue
        if resolved.is_file():
            return resolved, canonical_rel

    return None, clean


def view_image(image_path: str, tool_context: ToolContext) -> dict[str, Any]:
    """Inspect an image file natively using multimodal vision (plots in outputs/, uploaded images, or kb_images/ from PDFs).

    Call this tool after generating a plot with execute_python (e.g., 'outputs/trend.png')
    to verify the chart visually and read visual patterns, OR when search_knowledge_base
    returns an entry with type='image' and an 'image_path' (e.g., 'kb_images/doc1_p4_1.png').

    Args:
        image_path: Relative path such as 'outputs/chart.png', 'uploads/diagram.png', or 'kb_images/doc1_p3_1.png'.
    """
    session = getattr(tool_context, "session", None)
    session_id = getattr(session, "id", None) or "default"

    resolved, display_rel = _resolve_image_path(session_id, image_path)
    if resolved is None:
        return {
            "error": f"File gambar '{image_path}' tidak ditemukan di workspace maupun kb_images.",
            "hint": "Pastikan plot sudah disimpan ke 'outputs/<nama>.png' atau gunakan image_path persis dari hasil search_knowledge_base.",
        }

    try:
        with Image.open(resolved) as img:
            orig_width, orig_height = img.size
            if max(orig_width, orig_height) > MAX_IMAGE_DIMENSION:
                img.thumbnail((MAX_IMAGE_DIMENSION, MAX_IMAGE_DIMENSION), Image.Resampling.LANCZOS)
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="PNG", optimize=True)
            png_bytes = buf.getvalue()
            final_width, final_height = img.size
    except Exception as exc:
        return {"error": f"Gagal membaca gambar '{image_path}': {exc}"}

    queue_image_for_session(
        session_id=session_id,
        display_path=display_rel,
        image_bytes=png_bytes,
        mime_type="image/png",
    )

    return {
        "status": "loaded_into_vision",
        "image_path": display_rel,
        "dimensions": f"{final_width}x{final_height}",
        "markdown_embed": f"![{Path(display_rel).stem}]({display_rel})",
        "note": (
            "Gambar telah dimuat ke input visual Anda pada langkah ini. "
            "Jika ingin menampilkannya ke pengguna di jawaban akhir, sematkan dengan syntax Markdown: "
            f"![Deskripsi]({display_rel})"
        ),
    }
