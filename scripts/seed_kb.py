"""Seed the Knowledge Base with NTI Product Catalog PDF, Incident DOCX, Sales Policy TXT, and Pricelist CSV."""

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
            SEED_DOCS_DIR / "Katalog_Produk_dan_Solusi_NTI.pdf",
            "Katalog resmi produk & solusi PT Neural Technologies Indonesia (NTI): JustClick ERP (HCMS, eFinance, CRM, POS, SMART), diagram arsitektur modular, skema lisensi SaaS vs On-Premise, dan layanan IT Manpower Supply.",
        ),
        (
            SEED_DOCS_DIR / "Laporan_Investigasi_Insiden_Implementasi_Q4_2026.docx",
            "Laporan investigasi insiden Oktober 2026 (INC-NTI-2026-1012): analisis lonjakan churn dan kendala implementasi modul JustClick HCMS di sektor Manufaktur akibat update roster lembur v2.4 per 12 Oktober 2026.",
        ),
        (
            SEED_DOCS_DIR / "Kebijakan_Penjualan_SLA_dan_Diskon_NTI.txt",
            "Kebijakan harga lisensi JustClick ERP, diskon bundling multi-modul (10-20%), SLA waktu deployment per tier klien (14-60 hari), level dukungan eskalasi P1/P2/P3, dan kompensasi penalti SLA.",
        ),
        (
            SAMPLE_FILES_DIR / "pricelist_dan_paket_modul_nti.csv",
            "Daftar harga resmi lisensi modul JustClick ERP, biaya setup implementasi, minimum user, dan rate tenaga ahli manpower supply NTI (tersedia di workspace shared/pricelist_dan_paket_modul_nti.csv).",
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
