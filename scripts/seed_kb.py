"""Seed the Knowledge Base with the generated SOP PDF, Incident DOCX, Policy TXT, and shared CSV."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import DATA_DIR, SAMPLE_FILES_DIR
from app.kb.catalog import list_catalog_entries
from app.kb.ingest import remove_document_completely
from app.uploads import handle_kb_upload

SEED_DOCS_DIR = DATA_DIR / "seed_docs"


async def seed_all() -> None:
    # Clear existing global catalog entries so re-running seed_kb is idempotent
    existing = list_catalog_entries(include_all_sessions=True)
    for item in existing:
        remove_document_completely(int(item["id"]))

    items_to_seed = [
        (
            SEED_DOCS_DIR / "SOP_Penanganan_Transaksi_Gagal.pdf",
            "SOP resmi penanganan transaksi gagal (ERR_GW_TIMEOUT_504, ERR_INSUFFICIENT_FUNDS, ERR_INVALID_OTP, dll.), perbedaan prosedur tiap error, diagram alur eskalasi, dan rekonsiliasi H+1.",
        ),
        (
            SEED_DOCS_DIR / "Laporan_Insiden_Oktober_2026.docx",
            "Laporan investigasi insiden Oktober 2026 (INC-2026-1012): penyebab lonjakan transaksi gagal ERR_GW_TIMEOUT_504 di kanal API Partner & Virtual Account sejak 12 Oktober 2026 akibat migrasi gateway Bank Nusantara.",
        ),
        (
            SEED_DOCS_DIR / "Kebijakan_Retry_dan_Rekonsiliasi_Operasional.txt",
            "Kebijakan operasional auto-retry idempotency, standar waktu respons (latency), prosedur rekonsiliasi settlement H+1, dan SLA merchant Enterprise.",
        ),
        (
            SAMPLE_FILES_DIR / "biaya_mdr_per_kanal.csv",
            "Tabel struktur tarif MDR dan biaya tetap gateway per kanal pembayaran (tersedia langsung di workspace shared/biaya_mdr_per_kanal.csv).",
        ),
    ]

    for path, desc in items_to_seed:
        if not path.exists():
            raise FileNotFoundError(
                f"File seed tidak ditemukan: {path}. Jalankan scripts/generate_data.py terlebih dahulu."
            )
        print(f"Seeding {path.name} ...")
        res = await handle_kb_upload(
            filename=path.name,
            file_bytes=path.read_bytes(),
            description=desc,
            background=False,
        )
        print(f" -> Done: id={res['id']} kind={res['kind']} status={res['status']}")


if __name__ == "__main__":
    asyncio.run(seed_all())
