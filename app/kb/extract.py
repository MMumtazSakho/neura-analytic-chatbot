"""Document text and image extraction for PDF, DOCX, and TXT files."""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf as fitz  # PyMuPDF
from docx import Document as DocxDocument
from PIL import Image

from ..config import KB_IMAGES_DIR, ensure_runtime_dirs

MIN_IMAGE_WIDTH = 120
MIN_IMAGE_HEIGHT = 120


@dataclass
class ExtractedImage:
    page: int
    image_path: str  # relative path e.g. "kb_images/doc1_p2_1.png"
    abs_path: Path
    width: int
    height: int
    context_text: str = ""


@dataclass
class ExtractedPage:
    page: int
    text: str


@dataclass
class ExtractedDocument:
    pages: list[ExtractedPage] = field(default_factory=list)
    images: list[ExtractedImage] = field(default_factory=list)
    total_pages: int = 1
    total_chars: int = 0


def inspect_document_size(file_path: Path) -> tuple[int, int]:
    """Return (page_count, char_count) quickly for upload routing."""
    suffix = file_path.suffix.lower()
    if suffix == ".pdf":
        with fitz.open(file_path) as doc:
            pages = len(doc)
            chars = sum(len(page.get_text("text")) for page in doc)
            return pages, chars
    if suffix == ".docx":
        doc = DocxDocument(str(file_path))
        para_text = "\n".join(p.text for p in doc.paragraphs if p.text)
        table_text = "\n".join(
            " | ".join(cell.text.strip() for cell in row.cells)
            for table in doc.tables
            for row in table.rows
        )
        total_chars = len(para_text) + len(table_text)
        est_pages = max(1, (total_chars // 2200) + 1)
        return est_pages, total_chars
    # .txt or other text
    raw = read_text_file(file_path)
    total_chars = len(raw)
    est_pages = max(1, (total_chars // 2200) + 1)
    return est_pages, total_chars


def read_text_file(file_path: Path) -> str:
    try:
        return file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return file_path.read_text(encoding="latin-1", errors="replace")


def extract_document(file_path: Path, doc_id: int) -> ExtractedDocument:
    """Extract text pages and qualifying embedded images from PDF, DOCX, or TXT."""
    ensure_runtime_dirs()
    suffix = file_path.suffix.lower()
    if suffix == ".pdf":
        return _extract_pdf(file_path, doc_id)
    if suffix == ".docx":
        return _extract_docx(file_path, doc_id)
    return _extract_txt(file_path)


def _extract_pdf(file_path: Path, doc_id: int) -> ExtractedDocument:
    result = ExtractedDocument()
    seen_xrefs: set[int] = set()

    with fitz.open(file_path) as pdf:
        result.total_pages = len(pdf)
        for page_idx, page in enumerate(pdf, start=1):
            page_text = page.get_text("text").strip()
            if page_text:
                result.pages.append(ExtractedPage(page=page_idx, text=page_text))
                result.total_chars += len(page_text)

            img_list = page.get_images(full=True)
            img_counter = 0
            for img_info in img_list:
                xref = img_info[0]
                if xref in seen_xrefs:
                    continue
                seen_xrefs.add(xref)
                try:
                    base_img = pdf.extract_image(xref)
                    img_bytes = base_img.get("image")
                    if not img_bytes:
                        continue
                    with Image.open(io.BytesIO(img_bytes)) as pil_img:
                        w, h = pil_img.size
                        if w < MIN_IMAGE_WIDTH or h < MIN_IMAGE_HEIGHT:
                            continue
                        if pil_img.mode not in ("RGB", "RGBA"):
                            pil_img = pil_img.convert("RGB")
                        img_counter += 1
                        fname = f"doc{doc_id}_p{page_idx}_{img_counter}.png"
                        out_path = KB_IMAGES_DIR / fname
                        pil_img.save(out_path, format="PNG")
                        result.images.append(
                            ExtractedImage(
                                page=page_idx,
                                image_path=f"kb_images/{fname}",
                                abs_path=out_path,
                                width=w,
                                height=h,
                                context_text=page_text[:400],
                            )
                        )
                except Exception:
                    continue

    return result


def _extract_docx(file_path: Path, doc_id: int) -> ExtractedDocument:
    doc = DocxDocument(str(file_path))
    blocks: list[str] = []

    for p in doc.paragraphs:
        txt = p.text.strip()
        if txt:
            blocks.append(txt)

    for table in doc.tables:
        rows_md: list[str] = []
        for r_idx, row in enumerate(table.rows):
            cells = [c.text.strip().replace("\n", " ") for c in row.cells]
            rows_md.append("| " + " | ".join(cells) + " |")
            if r_idx == 0:
                rows_md.append("| " + " | ".join(["---"] * len(cells)) + " |")
        if rows_md:
            blocks.append("\n".join(rows_md))

    full_text = "\n\n".join(blocks)
    result = ExtractedDocument()
    # Group into virtual pages of ~2500 chars for citation clarity
    chunk_size = 2500
    if not full_text:
        result.total_pages = 1
        return result

    pages_text: list[str] = []
    current: list[str] = []
    current_len = 0
    for block in blocks:
        if current_len + len(block) > chunk_size and current:
            pages_text.append("\n\n".join(current))
            current = [block]
            current_len = len(block)
        else:
            current.append(block)
            current_len += len(block)
    if current:
        pages_text.append("\n\n".join(current))

    for idx, ptext in enumerate(pages_text, start=1):
        result.pages.append(ExtractedPage(page=idx, text=ptext))
    result.total_pages = len(pages_text)
    result.total_chars = len(full_text)

    # Also extract embedded images in DOCX relationships
    img_counter = 0
    for rel in doc.part.rels.values():
        if "image" in rel.target_ref:
            try:
                blob = rel.target_part.blob
                with Image.open(io.BytesIO(blob)) as pil_img:
                    w, h = pil_img.size
                    if w < MIN_IMAGE_WIDTH or h < MIN_IMAGE_HEIGHT:
                        continue
                    if pil_img.mode not in ("RGB", "RGBA"):
                        pil_img = pil_img.convert("RGB")
                    img_counter += 1
                    fname = f"doc{doc_id}_p1_{img_counter}.png"
                    out_path = KB_IMAGES_DIR / fname
                    pil_img.save(out_path, format="PNG")
                    result.images.append(
                        ExtractedImage(
                            page=1,
                            image_path=f"kb_images/{fname}",
                            abs_path=out_path,
                            width=w,
                            height=h,
                            context_text=full_text[:400],
                        )
                    )
            except Exception:
                continue

    return result


def _extract_txt(file_path: Path) -> ExtractedDocument:
    raw = read_text_file(file_path).strip()
    result = ExtractedDocument()
    if not raw:
        result.total_pages = 1
        return result

    paragraphs = [p.strip() for p in raw.split("\n\n") if p.strip()]
    pages_text: list[str] = []
    current: list[str] = []
    current_len = 0
    for para in paragraphs:
        if current_len + len(para) > 2500 and current:
            pages_text.append("\n\n".join(current))
            current = [para]
            current_len = len(para)
        else:
            current.append(para)
            current_len += len(para)
    if current:
        pages_text.append("\n\n".join(current))

    for idx, ptext in enumerate(pages_text, start=1):
        result.pages.append(ExtractedPage(page=idx, text=ptext))
    result.total_pages = len(pages_text)
    result.total_chars = len(raw)
    return result
