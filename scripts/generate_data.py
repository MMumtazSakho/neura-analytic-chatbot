"""Generate deterministic synthetic SQL database, PDF/DOCX/TXT documents, and sample spreadsheets
for PT Neural Technologies Indonesia (NTI) - Sales, Products, and Operations.
"""

from __future__ import annotations

import csv
import io
import random
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pymupdf as fitz  # PyMuPDF
import matplotlib
matplotlib.use("Agg")
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import pandas as pd
from docx import Document as DocxDocument

from app.config import (
    DATA_DIR,
    SAMPLE_FILES_DIR,
    TRANSACTIONS_DB_PATH,
    ensure_runtime_dirs,
)

SEED_DOCS_DIR = DATA_DIR / "seed_docs"


def generate_sqlite_database() -> None:
    """Create NTI sales & client database with 6 months of operational data and planted October anomaly."""
    ensure_runtime_dirs()
    if TRANSACTIONS_DB_PATH.exists():
        TRANSACTIONS_DB_PATH.unlink()

    rng = random.Random(42)
    conn = sqlite3.connect(str(TRANSACTIONS_DB_PATH))
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE clients (
            client_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            industry TEXT NOT NULL,
            city TEXT NOT NULL,
            tier TEXT NOT NULL,
            account_manager TEXT NOT NULL,
            onboarded_at TEXT NOT NULL
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE sales_orders (
            order_id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            order_date TEXT NOT NULL,
            order_month TEXT NOT NULL,
            client_id TEXT NOT NULL,
            client_name TEXT NOT NULL,
            industry TEXT NOT NULL,
            product_line TEXT NOT NULL,
            license_type TEXT NOT NULL,
            user_count INTEGER NOT NULL,
            amount INTEGER NOT NULL,
            status TEXT NOT NULL,
            loss_reason TEXT,
            deployment_days INTEGER NOT NULL,
            sales_rep TEXT NOT NULL,
            FOREIGN KEY (client_id) REFERENCES clients(client_id)
        )
        """
    )

    # Backward-compatibility views so queries using older names continue to work
    cur.execute(
        """
        CREATE VIEW transactions AS
        SELECT
            order_id AS tx_id,
            created_at,
            order_date AS tx_date,
            order_month AS tx_month,
            client_id AS merchant_id,
            product_line AS channel,
            license_type AS payment_method,
            sales_rep AS bank,
            industry AS merchant_category,
            amount,
            status,
            loss_reason AS error_code,
            deployment_days AS response_time_ms
        FROM sales_orders
        """
    )
    cur.execute(
        """
        CREATE VIEW merchants AS
        SELECT
            client_id AS merchant_id,
            name,
            industry AS category,
            city,
            tier,
            onboarded_at
        FROM clients
        """
    )

    industries = [
        "Manufacturing",
        "Plantation & Agro",
        "Education",
        "Healthcare",
        "Financial Services",
        "Retail & Distribution",
        "Gov & BUMD",
        "Mining & Energy",
    ]
    cities = ["Jakarta", "Surabaya", "Bandung", "Medan", "Balikpapan", "Semarang", "Makassar"]
    account_managers = ["Budi Santoso", "Dewi Lestari", "Rian Hidayat", "Siti Rahma", "Agus Setiawan"]

    company_prefixes = {
        "Manufacturing": ["PT Indo Manufaktur", "PT Sinar Baja Mandiri", "PT Presisi Logam Nusantara", "PT Rejoso Indo Pack"],
        "Plantation & Agro": ["PT Nusantara Agro Lestari", "PT Rejoso Manis Indo", "PT Sawit Mas Sejahtera", "PT Tani Jaya Abadi"],
        "Education": ["Yayasan Pendidikan Cendekia", "Universitas Bina Informatika", "Institut Teknologi Mandiri", "Sekolah Terpadu Nusantara"],
        "Healthcare": ["RS Medika Utama", "Klinik Sehat Terpadu", "RS Graha Kasih Sejahtera", "Lab Diagnostik Utama"],
        "Financial Services": ["BPR Dana Ventura", "KSP Makmur Bersama", "PT Multifinance Berkah", "Asuransi Terpadu Nusantara"],
        "Retail & Distribution": ["PT Retail Mega Niaga", "Distrindo Logistik Cepat", "PT Sumber Pangan Retail", "PT Mitra Niaga Terpadu"],
        "Gov & BUMD": ["Perumda Air Minum Sejahtera", "PT Penjaminan Daerah Mandiri", "BUMD Aneka Sarana", "Badan Layanan Umum Daerah Cendekia"],
        "Mining & Energy": ["PT Mineral Energi Utama", "PT Bumi Tambang Perkasa", "Nusantara Geo Resources", "PT Daya Mineral Abadi"],
    }

    clients: list[tuple[str, str, str, str, str, str, str]] = []
    for i in range(1, 151):
        cid = f"C{i:04d}"
        ind = industries[(i - 1) % len(industries)]
        pref = company_prefixes[ind][(i // len(industries)) % len(company_prefixes[ind])]
        cname = f"{pref} {i:03d}"
        city = cities[(i * 3) % len(cities)]
        tier = "Enterprise" if i <= 30 else ("Mid-Market" if i <= 95 else "SMB")
        am = account_managers[i % len(account_managers)]
        onboard_days_ago = rng.randint(180, 950)
        onboard_date = (date(2026, 5, 1) - timedelta(days=onboard_days_ago)).isoformat()
        clients.append((cid, cname, ind, city, tier, am, onboard_date))

    cur.executemany("INSERT INTO clients VALUES (?, ?, ?, ?, ?, ?, ?)", clients)

    # 6 months of daily orders / subscriptions: 2026-05-01 to 2026-10-31
    start_date = date(2026, 5, 1)
    end_date = date(2026, 10, 31)
    total_days = (end_date - start_date).days + 1

    product_lines = [
        "JustClick HCMS",
        "JustClick eFinance",
        "JustClick CRM",
        "JustClick Sales & POS",
        "JustClick SMART",
        "ERP Custom Implementation",
        "IT Staff Augmentation",
        "Corporate Training",
    ]

    base_prices = {
        "JustClick HCMS": (45_000, 180_000),             # per user/month, e.g. 50-300 users
        "JustClick eFinance": (35_000, 150_000),
        "JustClick CRM": (25_000, 90_000),
        "JustClick Sales & POS": (30_000, 120_000),
        "JustClick SMART": (20_000, 80_000),
        "ERP Custom Implementation": (150_000_000, 450_000_000),  # project lump sum
        "IT Staff Augmentation": (25_000_000, 65_000_000),         # monthly per staff
        "Corporate Training": (12_000_000, 45_000_000),           # per workshop
    }

    orders: list[tuple[str, str, str, str, str, str, str, str, str, int, int, str, str | None, int, str]] = []
    order_seq = 1

    for day_offset in range(total_days):
        current_day = start_date + timedelta(days=day_offset)
        iso_date = current_day.isoformat()
        month_str = iso_date[:7]
        day_of_month = current_day.day
        is_weekend = current_day.weekday() >= 5

        # Volume deals per day: 60 - 110 transactions/orders per day
        daily_count = rng.randint(45, 75) if is_weekend else rng.randint(85, 130)

        # Planted October anomaly starting Oct 12, 2026:
        # In Oct 2026 (>= Oct 12), JustClick HCMS in Manufacturing experiences severe churn & delays
        in_oct_anomaly = (month_str == "2026-10" and day_of_month >= 12)

        for _ in range(daily_count):
            oid = f"SO2026{order_seq:06d}"
            order_seq += 1

            client = rng.choice(clients)
            cid, cname, c_ind, c_city, c_tier, c_am, _ = client

            # Product selection
            if c_ind == "Education":
                prod = rng.choices(product_lines, weights=[20, 20, 10, 5, 35, 5, 3, 2])[0]
            elif c_ind == "Manufacturing":
                prod = rng.choices(product_lines, weights=[40, 25, 10, 10, 0, 10, 3, 2])[0]
            else:
                prod = rng.choices(product_lines, weights=[25, 25, 15, 15, 5, 8, 4, 3])[0]

            # License type
            if prod in ("ERP Custom Implementation", "Corporate Training"):
                lic_type = "Professional Services"
                user_cnt = rng.randint(10, 50)
                amount = rng.randint(base_prices[prod][0], base_prices[prod][1])
            elif prod == "IT Staff Augmentation":
                lic_type = "SaaS Monthly"
                user_cnt = rng.randint(1, 5)
                amount = rng.randint(base_prices[prod][0], base_prices[prod][1]) * user_cnt
            else:
                lic_type = rng.choices(["SaaS Monthly", "SaaS Annual", "On-Premise Perpetual"], weights=[60, 32, 8])[0]
                user_cnt = rng.randint(15, 350)
                price_per_user = rng.randint(base_prices[prod][0], base_prices[prod][1])
                if lic_type == "SaaS Annual":
                    amount = int(price_per_user * user_cnt * 12 * 0.85)  # 15% annual discount
                elif lic_type == "On-Premise Perpetual":
                    amount = int(price_per_user * user_cnt * 28)
                else:
                    amount = price_per_user * user_cnt

            sales_rep = rng.choice(account_managers)

            # Determine status & deployment duration
            # Normal baseline: ~88% Active/Won/Renewed, 4% Pending, 4% Churned/Cancelled
            if in_oct_anomaly and prod == "JustClick HCMS" and c_ind == "Manufacturing":
                # High failure/churn rate for HCMS in Manufacturing in mid-late October!
                status_roll = rng.random()
                if status_roll < 0.38:
                    status = "CHURNED"
                    loss_reason = "ERR_PAYROLL_CUSTOM_DELAY"
                    deploy_days = rng.randint(45, 75)
                elif status_roll < 0.52:
                    status = "CANCELLED"
                    loss_reason = "ERR_PAYROLL_CUSTOM_DELAY"
                    deploy_days = rng.randint(40, 65)
                elif status_roll < 0.65:
                    status = "PENDING_PAYMENT"
                    loss_reason = "DELAYED_GO_LIVE"
                    deploy_days = rng.randint(35, 60)
                else:
                    status = rng.choice(["ACTIVE", "WON", "RENEWED"])
                    loss_reason = None
                    deploy_days = rng.randint(30, 55)
            else:
                # Normal operational distribution
                status_roll = rng.random()
                if status_roll < 0.72:
                    status = "ACTIVE"
                    loss_reason = None
                    deploy_days = rng.randint(12, 28)
                elif status_roll < 0.88:
                    status = "WON" if rng.random() < 0.5 else "RENEWED"
                    loss_reason = None
                    deploy_days = rng.randint(10, 25)
                elif status_roll < 0.94:
                    status = "PENDING_PAYMENT"
                    loss_reason = None
                    deploy_days = rng.randint(15, 35)
                else:
                    status = "CHURNED" if rng.random() < 0.6 else "CANCELLED"
                    loss_reason = rng.choice([
                        "BUDGET_REALLOCATION",
                        "COMPETITOR_PRICE",
                        "INTERNAL_MANAGEMENT_CHANGE",
                        "FEATURE_GAP_SPECIALIZED",
                    ])
                    deploy_days = rng.randint(20, 45)

            # Random timestamp within the day
            sec = rng.randint(28800, 68400)  # 08:00 - 19:00 WIB
            h = sec // 3600
            m = (sec % 3600) // 60
            s = sec % 60
            created_at = f"{iso_date} {h:02d}:{m:02d}:{s:02d}"

            orders.append((
                oid,
                created_at,
                iso_date,
                month_str,
                cid,
                cname,
                c_ind,
                prod,
                lic_type,
                user_cnt,
                amount,
                status,
                loss_reason,
                deploy_days,
                sales_rep,
            ))

    cur.executemany(
        """
        INSERT INTO sales_orders VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        orders,
    )

    cur.execute("CREATE INDEX idx_so_month ON sales_orders(order_month)")
    cur.execute("CREATE INDEX idx_so_date ON sales_orders(order_date)")
    cur.execute("CREATE INDEX idx_so_prod ON sales_orders(product_line)")
    cur.execute("CREATE INDEX idx_so_status ON sales_orders(status)")
    cur.execute("CREATE INDEX idx_so_ind ON sales_orders(industry)")
    cur.execute("CREATE INDEX idx_so_loss ON sales_orders(loss_reason)")

    conn.commit()
    conn.close()
    print(f"[OK] Generated {len(orders):,} sales orders in {TRANSACTIONS_DB_PATH}")


def generate_diagram_bytes() -> bytes:
    """Generate high-resolution architecture diagram of JustClick ERP modular ecosystem."""
    fig, ax = plt.subplots(figsize=(10.5, 6.2), dpi=180)
    ax.set_xlim(0, 11)
    ax.set_ylim(0, 7.5)
    ax.axis("off")

    # Title
    ax.text(
        5.5,
        7.1,
        "Arsitektur Modular & Alur Data Terintegrasi JustClick ERP (PT Neural Technologies Indonesia)",
        ha="center",
        va="center",
        fontsize=11.5,
        fontweight="bold",
        color="#0F172A",
    )

    boxes = [
        # Center Core Database
        (
            3.8,
            3.8,
            3.4,
            1.5,
            "JustClick Core Database & Business Engine\n(Multi-Tenant Cloud SaaS / On-Premise)\n• Master Data Karyawan, Akun, & Pelanggan\n• Role-Based Access Control (RBAC) & Audit Trail",
            "#EFF6FF",
            "#1D4ED8",
        ),
        # Top Integration & External
        (
            3.8,
            5.6,
            3.4,
            1.1,
            "NTI Integration Gateway\n• Open API & Webhooks\n• Integrasi DJP e-Faktur & Payroll Bank",
            "#F8FAFC",
            "#475569",
        ),
        # Left HCMS
        (
            0.5,
            3.8,
            2.8,
            1.5,
            "JustClick HCMS\n(Human Capital Management)\n• Payroll Otomatis (PPh 21 TER, BPJS)\n• Multi-Shift Manufacturing Roster\n• Attendance Geofencing & Biometrik",
            "#F0FDF4",
            "#15803D",
        ),
        # Right eFinance
        (
            7.7,
            3.8,
            2.8,
            1.5,
            "JustClick eFinance\n(Financial & Accounting)\n• General Ledger & Multi-Currency\n• Jurnal Otomatis Gaji & Beban Operasional\n• Real-Time P&L & Cash Flow Reporting",
            "#FEF3C7",
            "#B45309",
        ),
        # Bottom Left CRM & Sales
        (
            0.8,
            1.4,
            3.0,
            1.6,
            "JustClick CRM & Sales Pipeline\n• Lead Scoring & Deal Tracking\n• Estimasi Quotation & Proposal B2B\n• Konversi Otomatis Menjadi Sales Order",
            "#FDF2F8",
            "#BE185D",
        ),
        # Bottom Center POS & Stock
        (
            4.2,
            1.4,
            2.6,
            1.6,
            "JustClick Sales & POS\n• Kasir Cloud & Offline Sync\n• Inventory Multi-Gudang\n• Replenishment Otomatis",
            "#ECFDF5",
            "#047857",
        ),
        # Bottom Right SMART (Education)
        (
            7.2,
            1.4,
            3.0,
            1.6,
            "JustClick SMART (Institusi Edukasi)\n• Registrasi Mahasiswa/Siswa\n• Billing SPP Terintegrasi Virtual Account\n• Nilai & Akademik Terpadu",
            "#FAF5FF",
            "#6B21A8",
        ),
    ]

    for x, y, w, h, label, facecolor, edgecolor in boxes:
        rect = mpatches.FancyBboxPatch(
            (x, y),
            w,
            h,
            boxstyle="round,pad=0.08,rounding_size=0.12",
            facecolor=facecolor,
            edgecolor=edgecolor,
            linewidth=1.6,
        )
        ax.add_patch(rect)
        ax.text(
            x + w / 2,
            y + h / 2,
            label,
            ha="center",
            va="center",
            fontsize=8.2,
            color="#0F172A",
            fontweight="medium",
        )

    arrow_style = dict(arrowstyle="<->", color="#334155", lw=1.5)
    # Connect Core to Top Gateway
    ax.annotate("", xy=(5.5, 5.3), xytext=(5.5, 5.6), arrowprops=arrow_style)
    # Connect HCMS to Core
    ax.annotate("", xy=(3.3, 4.55), xytext=(3.8, 4.55), arrowprops=arrow_style)
    # Connect eFinance to Core
    ax.annotate("", xy=(7.2, 4.55), xytext=(7.7, 4.55), arrowprops=arrow_style)
    # Connect CRM to Core
    ax.annotate("", xy=(2.3, 3.0), xytext=(4.3, 3.8), arrowprops=arrow_style)
    # Connect POS to Core
    ax.annotate("", xy=(5.5, 3.0), xytext=(5.5, 3.8), arrowprops=arrow_style)
    # Connect SMART to Core
    ax.annotate("", xy=(8.7, 3.0), xytext=(6.7, 3.8), arrowprops=arrow_style)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


def generate_seed_documents() -> None:
    """Create Knowledge Base seed documents for PT Neural Technologies Indonesia (NTI)."""
    SEED_DOCS_DIR.mkdir(parents=True, exist_ok=True)
    SAMPLE_FILES_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Katalog_Produk_dan_Solusi_NTI.pdf (8 pages with architecture diagram on page 4)
    pdf_path = SEED_DOCS_DIR / "Katalog_Produk_dan_Solusi_NTI.pdf"
    pdf = fitz.open()

    pages_content = [
        (
            "KATALOG RESMI PRODUK DAN SOLUSI TEKNOLOGI INFORMASI\n"
            "PT NEURAL TECHNOLOGIES INDONESIA (NTI)\n"
            "Dokumen Ref: NTI-CAT-2026-V2 | Edisi Revisi Terpadu\n\n"
            "1. TENTANG PERUSAHAAN\n"
            "PT Neural Technologies Indonesia (NTI) adalah perusahaan konsultan teknologi informasi dan penyedia sistem "
            "Enterprise Resource Planning (ERP) terintegrasi yang berkantor pusat di SOHO Pancoran - Splendor 1503, "
            "Jl. M.T. Haryono Kav. 2-3, Jakarta Selatan.\n\n"
            "NTI berfokus mendampingi korporasi besar, Badan Usaha Milik Daerah (BUMD), serta perusahaan berkembang dalam "
            "melakukan transformasi digital menyeluruh melalui automasi proses bisnis, integrasi lintas divisi, dan peningkatan efisiensi operasional.\n\n"
            "2. EMPAT PILAR LAYANAN UTAMA NTI\n"
            "A. JustClick ERP Suite: Solusi software bisnis all-in-one modular mencakup HCMS, eFinance, CRM, POS, dan SMART.\n"
            "B. IT Consulting & Custom Implementation: Perancangan IT roadmap strategis, kustomisasi Odoo ERP, dan integrasi API enterprise.\n"
            "C. IT Manpower Supply: Penyediaan tenaga ahli profesional (ERP Consultant, Fullstack Developer, Data Engineer, DevOps, QA).\n"
            "D. People Development & Training: Program pelatihan korporat NTI Academy untuk adopsi teknologi dan manajemen proses bisnis."
        ),
        (
            "3. LINI PRODUK FLAGSHIP: JUSTCLICK ERP\n\n"
            "JustClick ERP dirancang dengan pendekatan modular fleksibel sehingga perusahaan dapat mengadopsi modul yang dibutuhkan terlebih dahulu "
            "dan menambahkan modul lanjutan seiring pertumbuhan bisnis tanpa perlu mengganti sistem pondasi.\n\n"
            "A. JustClick HCMS (Human Capital Management System)\n"
            "   - Penggajian Otomatis (Payroll): Mendukung skema PPh 21 TER (Tarif Efektif Rata-rata), iuran BPJS Ketenagakerjaan & Kesehatan.\n"
            "   - Multi-Shift Roster: Pengaturan jadwal shift kerja kompleks 24/7 untuk industri manufaktur, perkebunan, dan rumah sakit.\n"
            "   - Attendance & ESS: Presensi biometrik fingerprint, face recognition, dan geofencing mobile app bagi pekerja lapangan.\n"
            "   - Manajemen Kinerja & KPI: Pelacakan OKR/KPI berkala serta kalkulasi bonus performa otomatis.\n\n"
            "B. JustClick eFinance (Accounting & Financial Management)\n"
            "   - Buku Besar Terpadu (General Ledger): Jurnal otomatis yang terhubung langsung ke transaksi penjualan, payroll, dan inventori.\n"
            "   - Manajemen Arus Kas & Rekonsiliasi Bank: Integrasi rekening koran dan pemantauan piutang (AR) serta utang usaha (AP).\n"
            "   - Laporan Finansial Real-Time: Neraca, Laba Rugi, dan Arus Kas konsolidasi multi-cabang."
        ),
        (
            "4. MODUL BISNIS LAINNYA DALAM JUSTCLICK ECOSYSTEM\n\n"
            "C. JustClick CRM & Sales Pipeline\n"
            "   - Pemantauan alur penjualan prospek B2B dari tahap Lead, Quotation, Negosiasi, hingga Closed Won.\n"
            "   - Integrasi langsung dengan modul eFinance saat deal disepakati untuk penerbitan Invoice otomatis.\n\n"
            "D. JustClick Sales & POS (Point of Sales)\n"
            "   - Software kasir modern untuk ritel, F&B, dan distributor dengan sinkronisasi online/offline.\n"
            "   - Manajemen persediaan multi-gudang (multi-warehouse) dengan peringatan stok kritis otomatis.\n\n"
            "E. JustClick SMART (Sistem Manajemen Sekolah & Perguruan Tinggi)\n"
            "   - Sistem terintegrasi untuk pendaftaran siswa/mahasiswa baru (PPDB/PMB), tagihan SPP berkala, dan kartu rencana studi.\n\n"
            "F. JustClick HSE (Health, Safety, and Environment)\n"
            "   - Pencatatan insiden K3, sertifikasi kepatuhan keselamatan kerja, dan audit berkala untuk industri manufaktur dan tambang."
        ),
        (
            "5. ARSITEKTUR INTEGRASI MODULAR JUSTCLICK ERP\n\n"
            "Berikut adalah diagram arsitektur keterhubungan modul dan pertukaran data secara terpusat pada platform JustClick ERP:"
        ),
        (
            "6. MODEL LISENSI DAN DEPLOYMENT\n\n"
            "NTI menyediakan fleksibilitas skema lisensi yang dapat disesuaikan dengan infrastruktur dan anggaran klien:\n\n"
            "1. Cloud SaaS Subscription (Bulanan / Tahunan)\n"
            "   - Di-host pada server cloud terkelola NTI dengan jaminan uptime 99.8%.\n"
            "   - Pembaruan sistem dan backup data harian otomatis tanpa biaya pemeliharaan tambahan.\n"
            "   - Minimum kontrak tahunan mendapatkan potongan harga khusus sebesar 15%.\n\n"
            "2. On-Premise Perpetual License (Private Server)\n"
            "   - Lisensi permanen yang diinstal pada infrastruktur server lokal atau private cloud klien.\n"
            "   - Cocok untuk institusi dengan kebijakan kedaulatan data ketat (BUMD, perbankan, dan manufaktur strategis).\n"
            "   - Termasuk garansi pemeliharaan software (SLA Maintenance) selama 12 bulan pertama."
        ),
        (
            "7. LAYANAN KONSULTASI DAN MANPOWER SUPPLY\n\n"
            "A. IT Staff Augmentation / Manpower Supply\n"
            "   - Penyediaan tenaga profesional siap pakai dengan durasi kontrak fleksibel (3, 6, atau 12 bulan).\n"
            "   - Kualifikasi: Senior ERP Functional Consultant, Fullstack Python/Odoo Developer, Data Analyst/BI Specialist.\n"
            "   - Jaminan penggantian talenta dalam waktu 7 hari kerja jika terdapat ketidaksesuaian kualifikasi teknis.\n\n"
            "B. NTI Academy Corporate Training\n"
            "   - Paket pelatihan in-house untuk transfer knowledge dan percepatan adopsi sistem oleh end-user.\n"
            "   - Modul pelatihan: Administrasi Payroll JustClick HCMS, Akuntansi Keuangan eFinance, dan Data Analytics SQL."
        ),
        (
            "8. PENERAPAN DI BERBAGAI SEKTOR INDUSTRI\n\n"
            "Solusi NTI telah dipercaya oleh lebih dari 150 klien di berbagai bidang:\n"
            "- Sektor Manufaktur & Industri Berat: Automasi shift kerja ribuan buruh pabrik dan kalkulasi lembur lintas hari.\n"
            "- Sektor Perkebunan & Agroindustri: Pemantauan mandor kebun, timbangan panen, dan distribusi logistik.\n"
            "- Sektor Pendidikan & Yayasan: Manajemen administrasi ribuan siswa dan rekonsiliasi SPP perbankan.\n"
            "- Sektor Kesehatan: Manajemen SDM tenaga medis, jadwal dokter, dan billing farmasi.\n"
            "- Sektor Jasa Keuangan & BUMD: Tata kelola kepatuhan regulasi finansial dan transparansi laporan anggaran."
        ),
        (
            "9. INFORMASI KONTAK DAN KEMITRAAN\n\n"
            "PT NEURAL TECHNOLOGIES INDONESIA\n"
            "SOHO Pancoran - Splendor 1503\n"
            "Jl. M.T. Haryono Kav. 2-3, Pancoran, Jakarta Selatan 12810\n"
            "Website: https://nti.co.id | https://justclick.id\n"
            "Email: sales@nti.co.id | info@nti.co.id\n"
            "Layanan Bantuan: Customer Helpdesk & Always On Support"
        ),
    ]

    for page_idx, text in enumerate(pages_content):
        page = pdf.new_page(width=595, height=842)  # A4
        rect = fitz.Rect(50, 50, 545, 792)
        page.insert_textbox(rect, text, fontsize=10.5, fontname="helv", lineheight=1.4)

        if page_idx == 3:  # Insert architecture diagram on page 4
            diagram_bytes = generate_diagram_bytes()
            img_rect = fitz.Rect(50, 200, 545, 520)
            page.insert_image(img_rect, stream=diagram_bytes)
            caption_rect = fitz.Rect(50, 530, 545, 560)
            page.insert_textbox(
                caption_rect,
                "Gambar 1: Diagram Arsitektur Modular dan Alur Sinkronisasi Data JustClick ERP Suite.",
                fontsize=9,
                fontname="helv",
            )

    pdf.save(str(pdf_path))
    pdf.close()
    print(f"[OK] Generated {pdf_path}")

    # 2. Laporan_Investigasi_Insiden_Implementasi_Q4_2026.docx
    docx_path = SEED_DOCS_DIR / "Laporan_Investigasi_Insiden_Implementasi_Q4_2026.docx"
    doc = DocxDocument()
    doc.add_heading("LAPORAN INVESTIGASI INSIDEN OPERASIONAL & CHURN KLIEN (INC-NTI-2026-1012)", level=1)

    doc.add_paragraph("Nomor Dokumen: INC-NTI-2026-1012\n" "Tanggal Terbit: 28 Oktober 2026\n" "Klasifikasi: Internal Confidential\n" "Penyusun: Tim Investigasi Kualitas Produk & Customer Success NTI")

    doc.add_heading("1. Ringkasan Eksekutif", level=2)
    doc.add_paragraph(
        "Memasuki kuartal IV tahun 2026, khususnya pada periode 12 Oktober 2026 hingga 26 Oktober 2026, "
        "terjadi anomali lonjakan pembatalan kontrak (churn / cancellation) serta penundaan pembayaran termin implementasi "
        "hingga 14.8% (ambang batas toleransi normal: <= 3.5%). "
        "Investigasi menemukan bahwa lonjakan ini terkonsentrasi secara spesifik pada lini produk JustClick HCMS "
        "di sektor Manufaktur, dengan kode alasan utama ERR_PAYROLL_CUSTOM_DELAY dan lonjakan waktu penyelesaian "
        "(deployment_days) rata-rata dari 22 hari menjadi 54 hari."
    )

    doc.add_heading("2. Kronologi dan Akar Masalah (Root Cause Analysis)", level=2)
    doc.add_paragraph(
        "Pada tanggal 12 Oktober 2026 pukul 02:00 WIB, tim engineering merilis update JustClick HCMS versi 2.4.0 "
        "yang memodifikasi modul kalkulasi pajak PPh 21 TER dan logika roster shift lintas hari (cross-day overtime shift). "
        "Meskipun update ini berhasil pada klien ritel dan perkebunan, implementasi pada sektor Manufaktur mengalami kendala berat:\n\n"
        "1. Kompleksitas Roster Lembur Pabrik: Logika baru gagal mengonsolidasikan jam lembur shift 3 (23:00 - 07:00 WIB) "
        "dengan integrasi mesin fingerprint ZKTeco di pabrik klien, menyebabkan ketidaksesuaian nominal rekapitulasi gaji.\n"
        "2. Akumulasi Tiket Eskalasi: Sebanyak 18 klien manufaktur tier Mid-Market dan Enterprise menahan pembayaran termin kedua "
        "dan 7 klien memutuskan membatalkan kontrak (status CHURNED) karena kekhawatiran keterlambatan penggajian karyawan pabrik."
    )

    doc.add_heading("3. Data Klien dan Produk yang Terdampak", level=2)
    doc.add_paragraph(
        "- Lini Produk Terdampak: JustClick HCMS (khusus modul multi-shift payroll manufaktur).\n"
        "- Lini Produk Tidak Terdampak: JustClick eFinance, JustClick CRM, IT Staff Augmentation, dan JustClick SMART "
        "tetap beroperasi normal dengan tingkat keberhasilan implementasi > 96%.\n"
        "- Sektor Terdampak: 92% kasus terjadi pada sektor Manufacturing di area Surabaya, Semarang, dan Bandung.\n"
        "- Kerugian Nilai Kontrak: Estimasi pembatalan dan penundaan pembayaran mencapai Rp 1.840.000.000."
    )

    doc.add_heading("4. Tindakan Mitigasi dan Solusi", level=2)
    doc.add_paragraph(
        "1. Rilis Patch Perbaikan v2.4.1 pada 26 Oktober 2026 untuk mengembalikan backward-compatibility integrasi fingerprint.\n"
        "2. Pembentukan Gugus Tugas Asistensi On-Site: Menugaskan 5 Senior ERP Consultant langsung ke lokasi pabrik klien terdampak.\n"
        "3. Penerapan Kompensasi SLA: Sesuai Kebijakan SLA NTI, klien manufaktur yang mengalami kendala diberikan kompensasi "
        "berupa perpanjangan lisensi SaaS gratis selama 1 bulan dan asistensi payroll run periode Oktober tanpa biaya tambahan."
    )
    doc.save(str(docx_path))
    print(f"[OK] Generated {docx_path}")

    # 3. Kebijakan_Penjualan_SLA_dan_Diskon_NTI.txt
    txt_path = SEED_DOCS_DIR / "Kebijakan_Penjualan_SLA_dan_Diskon_NTI.txt"
    txt_content = """\
DOKUMEN KEBIJAKAN PENJUALAN, PRICING TIER, DAN SERVICE LEVEL AGREEMENT (SLA)
PT NEURAL TECHNOLOGIES INDONESIA (NTI)
Nomor Kebijakan: POL-SALES-2026-V3
Berlaku Efektif: 1 Januari 2026

1. STRUKTUR HARGA LISENSI DAN KONTRAK
A. Skema Berlangganan Cloud SaaS:
   - JustClick HCMS: Rp 45.000 - Rp 65.000 per user/bulan (Minimum 25 user).
   - JustClick eFinance: Rp 35.000 - Rp 55.000 per user/bulan (Minimum 10 user).
   - JustClick CRM: Rp 25.000 - Rp 40.000 per user/bulan (Minimum 5 user).
   - JustClick Sales & POS: Rp 30.000 - Rp 45.000 per kasir/bulan.
   - JustClick SMART: Rp 20.000 - Rp 35.000 per siswa/tahun.

B. Skema Lisensi On-Premise Perpetual (Server Mandiri):
   - Nilai lisensi setara dengan 28x tarif bulanan SaaS per seat (one-time fee).
   - Biaya tahunan Software Maintenance & Security Patch: 18% dari nilai lisensi perpetual mulai tahun ke-2.

2. KEBIJAKAN DISKON MULTI-MODUL (BUNDLING INCENTIVE)
Untuk mendorong sinergi sistem antar divisi, NTI memberlakukan skema diskon resmi berikut:
- Bundling 2 Modul (Contoh: HCMS + eFinance): Diskon 10% dari total tagihan bulanan.
- Bundling 3 Modul atau Lebih (ERP Suite Penuh): Diskon 15% dari total tagihan lisensi.
- Komitmen Kontrak Tahunan (Annual Pre-Paid): Diskon tambahan 15% (dibayarkan 12 bulan di muka).
- Komitmen Multi-Tahun (3 Tahun): Diskon tetap 20% dan penguncian tarif harga anti-inflasi.

3. STANDAR WAKTU DEPLOYMENT DAN GO-LIVE (SLA DEPLOYMENT)
A. Tier SMB: Maksimal 14 hari kerja implementasi sejak penandatanganan kontrak.
B. Tier Mid-Market: Maksimal 30 hari kerja termasuk migrasi data saldo awal dan master karyawan.
C. Tier Enterprise: 45 - 60 hari kerja mencakup kustomisasi alur approval dan integrasi sistem eksternal.

4. SERVICE LEVEL AGREEMENT (SLA) DUKUNGAN TEKNIS DAN ESKALASI
- Tingkat Keparahan P1 (Kritis - Operasional Lumpuh / Payroll Gagal Total):
  * Waktu Respon Awal: Maksimal 30 menit.
  * Waktu Mitigasi / Workaround: Maksimal 4 jam.
  * Waktu Resolusi Penuh: Maksimal 24 jam.
- Tingkat Keparahan P2 (Tinggi - Salah satu fitur modul error namun ada alternatif):
  * Waktu Respon Awal: Maksimal 2 jam.
  * Waktu Resolusi: Maksimal 48 jam.
- Tingkat Keparahan P3 (Normal - Pertanyaan fitur / permintaan panduan penggunaan):
  * Waktu Respon: 1 hari kerja (08:00 - 18:00 WIB).

5. KEBIJAKAN KOMPENSASI PELANGGARAN SLA
Jika keterlambatan go-live (deployment_days) melampaui batas toleransi SLA akibat kendala teknis internal NTI:
- Keterlambatan 7 - 14 hari: Kompensasi berupa free maintenance 1 bulan.
- Keterlambatan > 14 hari: Kompensasi berupa free subscription lisensi 1 bulan dan asistensi on-site prioritas.
"""
    txt_path.write_text(txt_content, encoding="utf-8")
    print(f"[OK] Generated {txt_path}")

    # 4. pricelist_dan_paket_modul_nti.csv
    csv_path = SAMPLE_FILES_DIR / "pricelist_dan_paket_modul_nti.csv"
    pricelist_data = [
        {"kode_layanan": "JC-HCMS-STD", "kategori": "Software SaaS", "nama_layanan": "JustClick HCMS Standard", "biaya_lisensi_per_user_bln": 45000, "biaya_setup_implementasi": 7500000, "minimum_user": 20, "sla_support": "Standard (P2: 4h)", "deskripsi": "Fitur absensi online, cuti, klaim, dan rekap lembur standar"},
        {"kode_layanan": "JC-HCMS-ENT", "kategori": "Software SaaS", "nama_layanan": "JustClick HCMS Enterprise", "biaya_lisensi_per_user_bln": 65000, "biaya_setup_implementasi": 25000000, "minimum_user": 50, "sla_support": "Premium (P1: 30m)", "deskripsi": "Payroll PPh 21 TER, multi-shift 24/7 pabrik, KPI, ESS mobile app"},
        {"kode_layanan": "JC-FIN-STD", "kategori": "Software SaaS", "nama_layanan": "JustClick eFinance Starter", "biaya_lisensi_per_user_bln": 35000, "biaya_setup_implementasi": 5000000, "minimum_user": 5, "sla_support": "Standard (P2: 4h)", "deskripsi": "Buku besar, jurnal umum, laba rugi, dan pencatatan kas kecil"},
        {"kode_layanan": "JC-FIN-ENT", "kategori": "Software SaaS", "nama_layanan": "JustClick eFinance Enterprise", "biaya_lisensi_per_user_bln": 55000, "biaya_setup_implementasi": 20000000, "minimum_user": 15, "sla_support": "Premium (P1: 30m)", "deskripsi": "Multi-cabang, rekonsiliasi bank otomatis, pajak e-Faktur, AP/AR"},
        {"kode_layanan": "JC-CRM-PRO", "kategori": "Software SaaS", "nama_layanan": "JustClick CRM Professional", "biaya_lisensi_per_user_bln": 30000, "biaya_setup_implementasi": 5000000, "minimum_user": 5, "sla_support": "Standard (P2: 4h)", "deskripsi": "Sales pipeline, lead scoring, quotation, integrasi WhatsApp"},
        {"kode_layanan": "JC-POS-RETAIL", "kategori": "Software SaaS", "nama_layanan": "JustClick Sales & POS", "biaya_lisensi_per_user_bln": 35000, "biaya_setup_implementasi": 6000000, "minimum_user": 3, "sla_support": "Standard (P2: 4h)", "deskripsi": "Aplikasi kasir multi-outlet, barcode scanner, stok inventori"},
        {"kode_layanan": "JC-SMART-EDU", "kategori": "Software SaaS", "nama_layanan": "JustClick SMART Education", "biaya_lisensi_per_user_bln": 25000, "biaya_setup_implementasi": 15000000, "minimum_user": 100, "sla_support": "Dedicated Academic", "deskripsi": "Sistem informasi akademik, SPP online, raport digital siswa"},
        {"kode_layanan": "NTI-CUST-ERP", "kategori": "Consulting", "nama_layanan": "ERP Custom Implementation", "biaya_lisensi_per_user_bln": 0, "biaya_setup_implementasi": 185000000, "minimum_user": 1, "sla_support": "Dedicated Squad", "deskripsi": "Pengembangan modul kustom proses bisnis unik dan integrasi API"},
        {"kode_layanan": "NTI-MP-SR-DEV", "kategori": "Manpower Supply", "nama_layanan": "Senior Fullstack / ERP Dev", "biaya_lisensi_per_user_bln": 35000000, "biaya_setup_implementasi": 0, "minimum_user": 1, "sla_support": "Daily Standup", "deskripsi": "Penempatan tenaga ahli pengembang sistem durasi kontrak per bulan"},
        {"kode_layanan": "NTI-MP-CONS", "kategori": "Manpower Supply", "nama_layanan": "Lead ERP Functional Consultant", "biaya_lisensi_per_user_bln": 45000000, "biaya_setup_implementasi": 0, "minimum_user": 1, "sla_support": "Daily Standup", "deskripsi": "Konsultan analis proses bisnis & perancang alur sistem ERP"},
        {"kode_layanan": "NTI-TRN-CORP", "kategori": "Training", "nama_layanan": "In-House Corporate Bootcamp", "biaya_lisensi_per_user_bln": 18000000, "biaya_setup_implementasi": 0, "minimum_user": 1, "sla_support": "Workshop 3 Hari", "deskripsi": "Pelatihan intensif adopsi sistem ERP, SQL analytics & best practices"},
    ]

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(pricelist_data[0].keys()))
        writer.writeheader()
        writer.writerows(pricelist_data)
    print(f"[OK] Generated {csv_path}")


if __name__ == "__main__":
    generate_sqlite_database()
    generate_seed_documents()
