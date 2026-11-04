"""Generate deterministic synthetic SQL database, PDF/DOCX/TXT documents, and sample spreadsheets."""

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
    """Create transactions.db with 6 months of operational data and a planted October anomaly."""
    ensure_runtime_dirs()
    if TRANSACTIONS_DB_PATH.exists():
        TRANSACTIONS_DB_PATH.unlink()

    rng = random.Random(42)
    conn = sqlite3.connect(str(TRANSACTIONS_DB_PATH))
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE merchants (
            merchant_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            city TEXT NOT NULL,
            tier TEXT NOT NULL,
            onboarded_at TEXT NOT NULL
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE transactions (
            tx_id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            tx_date TEXT NOT NULL,
            tx_month TEXT NOT NULL,
            merchant_id TEXT NOT NULL,
            channel TEXT NOT NULL,
            payment_method TEXT NOT NULL,
            bank TEXT NOT NULL,
            merchant_category TEXT NOT NULL,
            amount INTEGER NOT NULL,
            status TEXT NOT NULL,
            error_code TEXT,
            response_time_ms INTEGER NOT NULL,
            FOREIGN KEY (merchant_id) REFERENCES merchants(merchant_id)
        )
        """
    )

    categories = [
        "F&B",
        "Retail",
        "E-Commerce",
        "Travel",
        "Utilities",
        "Digital Services",
    ]
    cities = ["Jakarta", "Surabaya", "Bandung", "Medan", "Semarang", "Makassar", "Denpasar"]
    tiers = ["Enterprise", "Mid-Market", "SMB"]

    merchants: list[tuple[str, str, str, str, str, str]] = []
    for i in range(1, 121):
        mid = f"M{i:04d}"
        cat = categories[(i - 1) % len(categories)]
        city = cities[(i * 3) % len(cities)]
        tier = "Enterprise" if i <= 20 else ("Mid-Market" if i <= 65 else "SMB")
        onboard_days_ago = rng.randint(180, 900)
        onboard_date = (date(2026, 5, 1) - timedelta(days=onboard_days_ago)).isoformat()
        mname = f"{cat.replace(' ', '')} Partner {i:03d}"
        merchants.append((mid, mname, cat, city, tier, onboard_date))

    cur.executemany(
        "INSERT INTO merchants VALUES (?, ?, ?, ?, ?, ?)",
        merchants,
    )

    channels_methods = {
        "QRIS": ["QRIS Dynamic", "QRIS Static"],
        "Mobile App": ["In-App Wallet", "One-Click Card", "Mobile VA"],
        "Web": ["Web Checkout Card", "Web Direct Debit"],
        "Virtual Account": ["VA Transfer", "VA Closed Amount"],
        "API Partner": ["Host-to-Host API", "Direct Disbursement API"],
    }
    banks = [
        "Bank Nusantara",
        "Bank Mandiri Jaya",
        "Bank Sentral Niaga",
        "Bank Prima Digital",
        "WalletPay Gateway",
    ]

    # Amount range by category
    amount_ranges = {
        "F&B": (25_000, 350_000),
        "Retail": (50_000, 1_200_000),
        "E-Commerce": (45_000, 2_500_000),
        "Travel": (350_000, 6_500_000),
        "Utilities": (75_000, 950_000),
        "Digital Services": (20_000, 500_000),
    }

    # Base daily volume per month (May to Oct 2026)
    month_daily_base = {
        5: 460,
        6: 485,
        7: 510,
        8: 525,
        9: 555,
        10: 580,
    }

    # Channel weights by month (QRIS grows steadily)
    channel_weights_by_month = {
        5: [("QRIS", 0.24), ("Mobile App", 0.25), ("Web", 0.16), ("Virtual Account", 0.19), ("API Partner", 0.16)],
        6: [("QRIS", 0.25), ("Mobile App", 0.25), ("Web", 0.15), ("Virtual Account", 0.19), ("API Partner", 0.16)],
        7: [("QRIS", 0.27), ("Mobile App", 0.24), ("Web", 0.15), ("Virtual Account", 0.18), ("API Partner", 0.16)],
        8: [("QRIS", 0.28), ("Mobile App", 0.24), ("Web", 0.14), ("Virtual Account", 0.18), ("API Partner", 0.16)],
        9: [("QRIS", 0.29), ("Mobile App", 0.23), ("Web", 0.14), ("Virtual Account", 0.18), ("API Partner", 0.16)],
        10: [("QRIS", 0.30), ("Mobile App", 0.22), ("Web", 0.13), ("Virtual Account", 0.18), ("API Partner", 0.17)],
    }

    # Category weights by month (F&B & Digital Services grow in Sep-Oct; Travel peaks in Jun-Jul then drops in Aug-Oct)
    cat_weights_by_month = {
        5: [("F&B", 0.22), ("E-Commerce", 0.24), ("Retail", 0.18), ("Travel", 0.14), ("Utilities", 0.11), ("Digital Services", 0.11)],
        6: [("F&B", 0.22), ("E-Commerce", 0.23), ("Retail", 0.17), ("Travel", 0.17), ("Utilities", 0.10), ("Digital Services", 0.11)],
        7: [("F&B", 0.22), ("E-Commerce", 0.23), ("Retail", 0.17), ("Travel", 0.18), ("Utilities", 0.10), ("Digital Services", 0.10)],
        8: [("F&B", 0.24), ("E-Commerce", 0.25), ("Retail", 0.18), ("Travel", 0.11), ("Utilities", 0.10), ("Digital Services", 0.12)],
        9: [("F&B", 0.27), ("E-Commerce", 0.25), ("Retail", 0.17), ("Travel", 0.09), ("Utilities", 0.10), ("Digital Services", 0.12)],
        10: [("F&B", 0.29), ("E-Commerce", 0.25), ("Retail", 0.17), ("Travel", 0.07), ("Utilities", 0.09), ("Digital Services", 0.13)],
    }

    merchants_by_cat: dict[str, list[tuple[str, str, str, str, str, str]]] = {}
    for m in merchants:
        merchants_by_cat.setdefault(m[2], []).append(m)

    normal_errors = [
        ("ERR_INSUFFICIENT_FUNDS", 0.42),
        ("ERR_INVALID_OTP", 0.26),
        ("ERR_LIMIT_EXCEEDED", 0.16),
        ("ERR_GW_TIMEOUT_504", 0.10),
        ("ERR_BANK_DOWN", 0.06),
    ]

    def pick_weighted(items_weights: list[tuple[str, float]]) -> str:
        r = rng.random()
        acc = 0.0
        for item, w in items_weights:
            acc += w
            if r <= acc:
                return item
        return items_weights[-1][0]

    start_day = date(2026, 5, 1)
    end_day = date(2026, 10, 31)
    anomaly_start = date(2026, 10, 12)

    tx_rows: list[tuple] = []
    seq = 0
    curr_day = start_day

    while curr_day <= end_day:
        m = curr_day.month
        base_n = month_daily_base[m]
        # Slight weekend bump
        if curr_day.weekday() >= 4:
            base_n = int(base_n * 1.08)
        daily_n = base_n + rng.randint(-20, 20)

        ch_weights = channel_weights_by_month[m]
        cat_weights = cat_weights_by_month[m]
        tx_date_str = curr_day.isoformat()
        tx_month_str = curr_day.strftime("%Y-%m")

        for _ in range(daily_n):
            seq += 1
            tx_id = f"TX{curr_day.strftime('%Y%m')}{seq:06d}"
            hh = rng.randint(0, 23)
            mm = rng.randint(0, 59)
            ss = rng.randint(0, 59)
            created_at = f"{tx_date_str} {hh:02d}:{mm:02d}:{ss:02d}"

            channel = pick_weighted(ch_weights)
            cat = pick_weighted(cat_weights)
            m_tuple = rng.choice(merchants_by_cat[cat])
            merchant_id = m_tuple[0]
            payment_method = rng.choice(channels_methods[channel])

            # Bank routing weights
            if channel in ("API Partner", "Virtual Account"):
                bank = pick_weighted(
                    [
                        ("Bank Nusantara", 0.48),
                        ("Bank Mandiri Jaya", 0.20),
                        ("Bank Sentral Niaga", 0.16),
                        ("Bank Prima Digital", 0.10),
                        ("WalletPay Gateway", 0.06),
                    ]
                )
            else:
                bank = pick_weighted(
                    [
                        ("Bank Nusantara", 0.22),
                        ("Bank Mandiri Jaya", 0.25),
                        ("Bank Sentral Niaga", 0.20),
                        ("Bank Prima Digital", 0.15),
                        ("WalletPay Gateway", 0.18),
                    ]
                )

            low_amt, high_amt = amount_ranges[cat]
            amount = round(rng.randint(low_amt, high_amt), -2)

            # Determine status, error_code, and response_time_ms
            is_oct_anomaly_window = (
                curr_day >= anomaly_start
                and channel in ("API Partner", "Virtual Account")
                and bank == "Bank Nusantara"
            )

            if is_oct_anomaly_window:
                # High failure rate during Bank Nusantara gateway timeout issue
                fail_prob = 0.34 if channel == "API Partner" else 0.29
                pend_prob = 0.02
                r_stat = rng.random()
                if r_stat < fail_prob:
                    status = "FAILED"
                    if rng.random() < 0.88:
                        error_code = "ERR_GW_TIMEOUT_504"
                        response_time_ms = rng.randint(9400, 10050)
                    else:
                        error_code = pick_weighted(normal_errors)
                        response_time_ms = rng.randint(1200, 3500)
                elif r_stat < fail_prob + pend_prob:
                    status = "PENDING"
                    error_code = None
                    response_time_ms = rng.randint(3500, 7500)
                else:
                    status = "SUCCESS"
                    error_code = None
                    response_time_ms = rng.randint(1100, 3800)
            else:
                # Normal baseline operation (~3.1% failure rate)
                fail_prob = 0.031
                pend_prob = 0.009
                r_stat = rng.random()
                if r_stat < fail_prob:
                    status = "FAILED"
                    error_code = pick_weighted(normal_errors)
                    if error_code == "ERR_GW_TIMEOUT_504":
                        response_time_ms = rng.randint(4500, 8500)
                    else:
                        response_time_ms = rng.randint(320, 1100)
                elif r_stat < fail_prob + pend_prob:
                    status = "PENDING"
                    error_code = None
                    response_time_ms = rng.randint(600, 1800)
                else:
                    status = "SUCCESS"
                    error_code = None
                    response_time_ms = rng.randint(180, 680)

            tx_rows.append(
                (
                    tx_id,
                    created_at,
                    tx_date_str,
                    tx_month_str,
                    merchant_id,
                    channel,
                    payment_method,
                    bank,
                    cat,
                    amount,
                    status,
                    error_code,
                    response_time_ms,
                )
            )

        curr_day += timedelta(days=1)

    cur.executemany(
        "INSERT INTO transactions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        tx_rows,
    )

    cur.execute("CREATE INDEX idx_tx_month ON transactions(tx_month)")
    cur.execute("CREATE INDEX idx_tx_date ON transactions(tx_date)")
    cur.execute("CREATE INDEX idx_tx_channel ON transactions(channel)")
    cur.execute("CREATE INDEX idx_tx_status ON transactions(status)")
    cur.execute("CREATE INDEX idx_tx_error ON transactions(error_code)")
    cur.execute("CREATE INDEX idx_tx_bank ON transactions(bank)")
    cur.execute("CREATE INDEX idx_tx_cat ON transactions(merchant_category)")

    conn.commit()
    conn.close()
    print(f"[OK] Generated {len(tx_rows):,} transactions in {TRANSACTIONS_DB_PATH}")


def _build_escalation_flowchart_png() -> bytes:
    """Render a clean operational flowchart diagram for failed transaction handling."""
    fig, ax = plt.subplots(figsize=(10, 6.2), dpi=160)
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 6.5)
    ax.axis("off")

    fig.patch.set_facecolor("#FFFFFF")
    ax.set_title(
        "Diagram Alur Penanganan & Eskalasi Transaksi Gagal (SOP-OPS-04)",
        fontsize=13,
        fontweight="bold",
        pad=12,
        color="#111827",
    )

    boxes = [
        (3.6, 5.4, 2.8, 0.75, "Transaksi FAILED Terdeteksi\n(Alert Monitoring / Webhook)", "#EFF6FF", "#1D4ED8"),
        (3.6, 4.1, 2.8, 0.75, "Klasifikasi Kode Error\n(Cek Kolom error_code & bank)", "#F9FAFB", "#374151"),
        (0.4, 2.5, 2.6, 0.95, "ERR_GW_TIMEOUT_504 /\nERR_BANK_DOWN (Teknis)\n• Auto-Retry maks 2x (backoff)\n• Cek Status Callback Gateway", "#FEF2F2", "#B91C1C"),
        (3.7, 2.5, 2.6, 0.95, "ERR_INSUFFICIENT_FUNDS /\nERR_LIMIT_EXCEEDED (Nasabah)\n• Dilarang Auto-Retry\n• Kirim Notifikasi Ganti Metode", "#FFFBEB", "#B45309"),
        (7.0, 2.5, 2.6, 0.95, "ERR_INVALID_OTP\n(Autentikasi / Keamanan)\n• Minta Input Ulang OTP (maks 3x)\n• Cooldown 15 Menit jika Gagal", "#F0FDF4", "#15803D"),
        (0.4, 0.7, 4.2, 1.0, "Eskalasi L2/L3 & Rekonsiliasi H+1\n• Jika Timeout > 5% di 1 Bank Gateway: P1 Incident\n• Reroute trafik ke Backup Gateway / Naikkan Timeout\n• Rekonsiliasi Settlement H+1 sebelum Refund", "#EFF6FF", "#1E40AF"),
        (5.4, 0.7, 4.2, 1.0, "Penutupan Tiket & Komunikasi Merchant\n• Update Status di Dashboard Merchant\n• Catat di Log Insiden Operasional jika sistemik", "#F3F4F6", "#374151"),
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
            fontsize=8.5,
            color="#111827",
            fontweight="medium",
        )

    arrow_style = dict(arrowstyle="->", color="#4B5563", lw=1.5)
    # Arrows
    ax.annotate("", xy=(5.0, 4.85), xytext=(5.0, 5.4), arrowprops=arrow_style)
    ax.annotate("", xy=(1.7, 3.45), xytext=(4.2, 4.1), arrowprops=arrow_style)
    ax.annotate("", xy=(5.0, 3.45), xytext=(5.0, 4.1), arrowprops=arrow_style)
    ax.annotate("", xy=(8.3, 3.45), xytext=(5.8, 4.1), arrowprops=arrow_style)
    ax.annotate("", xy=(2.1, 1.7), xytext=(1.7, 2.5), arrowprops=arrow_style)
    ax.annotate("", xy=(7.0, 1.7), xytext=(5.0, 2.5), arrowprops=arrow_style)
    ax.annotate("", xy=(8.1, 1.7), xytext=(8.3, 2.5), arrowprops=arrow_style)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


def generate_seed_documents() -> None:
    """Create the 3 Knowledge Base seed documents (8-page PDF with diagram, DOCX incident report, TXT policy)."""
    SEED_DOCS_DIR.mkdir(parents=True, exist_ok=True)

    # 1. SOP_Penanganan_Transaksi_Gagal.pdf (8 pages with embedded diagram on page 4)
    pdf_path = SEED_DOCS_DIR / "SOP_Penanganan_Transaksi_Gagal.pdf"
    pdf = fitz.open()

    pages_content = [
        (
            "DOKUMEN STANDAR OPERASIONAL PROSEDUR (SOP-OPS-04)\n"
            "PENANGANAN TRANSAKSI GAGAL DAN ESKALASI GATEWAY PEMBAYARAN\n\n"
            "1. TUJUAN DAN RUANG LINGKUP\n"
            "Dokumen ini menetapkan prosedur standar bagi Tim Operasional Pembayaran (Payment Operations), "
            "Customer Support Merchant, dan Site Reliability Engineering (SRE) dalam menangani transaksi berstatus "
            "FAILED maupun PENDING yang melampaui ambang SLA pada seluruh kanal pembayaran (QRIS, Mobile App, Web, "
            "Virtual Account, dan API Partner).\n\n"
            "2. INDIKATOR KINERJA UTAMA (SLA OPERASIONAL)\n"
            "- Target Tingkat Keberhasilan Transaksi (Success Rate) Global: >= 95.5%\n"
            "- Batas Ambang Normal Kegagalan (Failure Rate Baseline): <= 3.5%\n"
            "- Ambang Peringatan Dini (Warning Threshold): Failure Rate > 5.0% pada satu kanal selama 30 menit\n"
            "- Ambang Insiden Kritis (P1 Major Incident): Failure Rate > 10.0% pada kanal utama atau waktu respons "
            "rata-rata (response_time_ms) melampaui 5.000 ms."
        ),
        (
            "3. KLASIFIKASI PENYEBAB UMUM KEGAGALAN TRANSAKSI\n\n"
            "Berdasarkan analisis historis sistem payment gateway perusahaan, penyebab kegagalan transaksi dibagi "
            "menjadi tiga kelompok utama:\n\n"
            "A. Kegagalan Sisi Nasabah / Pemegang Akun (User-Side Failures)\n"
            "1) ERR_INSUFFICIENT_FUNDS: Saldo rekening, e-wallet, atau limit kartu nasabah tidak mencukupi untuk "
            "memenuhi nominal transaksi. Merupakan penyebab kegagalan harian paling umum pada kondisi operasional normal (~40-45% dari total gagal normal).\n"
            "2) ERR_LIMIT_EXCEEDED: Transaksi melampaui batas maksimal nominal harian atau frekuensi transaksi per hari "
            "yang ditetapkan oleh bank penerbit.\n\n"
            "B. Kegagalan Autentikasi & Keamanan (Authentication Failures)\n"
            "3) ERR_INVALID_OTP: Nasabah salah memasukkan kode One-Time Password (OTP) atau PIN melampaui batas waktu "
            "validitas (3 menit).\n\n"
            "C. Kegagalan Teknis Infrastruktur & Bank Gateway (System/Gateway Failures)\n"
            "4) ERR_GW_TIMEOUT_504: Gateway pembayaran tidak menerima respons dari sistem core bank mitra dalam batas "
            "waktu baca (read timeout). Sering dipicu oleh lonjakan latensi jaringan, perubahan konfigurasi timeout pada API bank mitra, "
            "atau antrean proses pada kanal integrasi langsung seperti API Partner dan Virtual Account.\n"
            "5) ERR_BANK_DOWN: Sistem bank mitra sedang mengalami gangguan penuh atau pemeliharaan terjadwal (scheduled maintenance)."
        ),
        (
            "4. PERBEDAAN PROSEDUR PENANGANAN UNTUK SETIAP JENIS KEGAGALAN\n\n"
            "Setiap kode error memiliki karakteristik risiko yang berbeda (terutama risiko double-charge / pendebitan ganda), "
            "sehingga prosedur penanganannya TIDAK BOLEH disamakan:\n\n"
            "1) Prosedur untuk ERR_GW_TIMEOUT_504 (Gateway Timeout)\n"
            "   - Sifat: Status akhir di sisi bank mitra belum pasti (bisa sebenarnya sukses terdebet di bank namun respons terputus).\n"
            "   - Tindakan Sistem: Jalankan Inquiry Status API ke bank mitra terlebih dahulu. Auto-retry hanya boleh dilakukan maksimal "
            "2 kali menggunakan idempotency key yang sama dengan jeda exponential backoff (2 detik, lalu 5 detik).\n"
            "   - Larangan: Dilarang melakukan refund instan sebelum hasil Inquiry Status atau Rekonsiliasi Settlement H+1 memastikan dana mengendap.\n"
            "   - Tindakan Infrastruktur: Jika ERR_GW_TIMEOUT_504 meningkat drastis pada bank tertentu, tim SRE wajib memeriksa konfigurasi "
            "batas timeout koneksi (pastikan minimal 30 detik untuk endpoint sinkron) dan mengaktifkan rerouting ke jalur gateway cadangan.\n\n"
            "2) Prosedur untuk ERR_BANK_DOWN (Gangguan Bank Mitra)\n"
            "   - Sifat: Koneksi ditolak langsung oleh bank mitra (HTTP 502/503).\n"
            "   - Tindakan: Aktifkan fitur Circuit Breaker otomatis untuk mengalihkan trafik ke bank/acquirer sekunder atau tampilkan banner "
            "pemeliharaan pada halaman pembayaran untuk metode bank terkait."
        ),
        (
            "5. DIAGRAM ALUR PENANGANAN DAN ESKALASI TRANSAKSI GAGAL\n\n"
            "Berikut adalah diagram alur resmi pengambilan keputusan operasional berdasarkan klasifikasi kode error "
            "dan ambang batas eskalasi insiden:"
        ),
        (
            "6. PROSEDUR PENANGANAN KEGAGALAN SISI NASABAH & AUTENTIKASI\n\n"
            "3) Prosedur untuk ERR_INSUFFICIENT_FUNDS (Saldo Tidak Cukup)\n"
            "   - Sifat: Penolakan tegas (hard decline) dari bank penerbit.\n"
            "   - Tindakan: DILARANG melakukan auto-retry sistem karena akan membebani kuota API bank dan pasti ditolak kembali. "
            "Tampilkan pesan ramah kepada pengguna untuk melakukan top-up saldo atau memilih metode pembayaran alternatif (mis. pindah ke QRIS atau Virtual Account lain).\n\n"
            "4) Prosedur untuk ERR_LIMIT_EXCEEDED (Melebihi Limit Harian)\n"
            "   - Sifat: Penolakan kebijakan limit bank penerbit.\n"
            "   - Tindakan: Dilarang auto-retry. Sarankan pengguna memecah pembayaran (split bill jika didukung merchant) atau menggunakan kanal "
            "Virtual Account / kartu bank lain.\n\n"
            "5) Prosedur untuk ERR_INVALID_OTP (Kesalahan OTP)\n"
            "   - Sifat: Kegagalan verifikasi pengguna.\n"
            "   - Tindakan: Izinkan pengiriman ulang (resend OTP) maksimal 3 kali dalam jendela 10 menit. Jika gagal 3 kali berturut-turut, "
            "bekukan sementara sesi transaksi selama 15 menit untuk mencegah serangan brute-force dan arahkan ke tim Fraud Risk jika mencurigakan."
        ),
        (
            "7. MATRIKS ESKALASI INSIDEN OPERASIONAL\n\n"
            "| Level | Kondisi Pemicu | Target Respons | Tim Penanggung Jawab | Tindakan Utama |\n"
            "|---|---|---|---|---|\n"
            "| L1 (Normal) | Failure rate < 3.5%, error didominasi ERR_INSUFFICIENT_FUNDS | 4 Jam | L1 Payment Ops | Monitoring rutin & laporan harian |\n"
            "| L2 (Warning) | Failure rate 5% - 10% pada 1 kanal/bank selama >= 15 menit | 30 Menit | L2 Ops & Integration Engineer | Cek latensi gateway, hubungi PIC teknis bank mitra |\n"
            "| L3 (Critical P1) | Failure rate > 10% atau lonjakan ERR_GW_TIMEOUT_504 masif | 10 Menit | SRE Lead, Head of Payment, Bank PIC | Rollback/naikkan timeout, throttle rate-limit, reroute gateway |\n\n"
            "Catatan Penting untuk Kanal API Partner & Virtual Account:\n"
            "Kanal API Partner dan Virtual Account sering digunakan oleh merchant Enterprise dengan trafik burst tinggi. "
            "Apabila terjadi perubahan parameter timeout atau rate-limit pada sisi bank gateway, kedua kanal ini akan menjadi "
            "kanal pertama yang mengalami lonjakan ERR_GW_TIMEOUT_504."
        ),
        (
            "8. PROSEDUR REKONSILIASI H+1 DAN PENANGANAN SUSPECT / PENDING\n\n"
            "1. Setiap pukul 06:00 WIB (H+1), sistem Settlement melakukan pencocokan otomatis antara tabel `transactions` "
            "internal dengan file laporan mutasi (MT940 / CSV Settlement) dari masing-masing bank mitra.\n"
            "2. Apabila ditemukan transaksi berstatus FAILED dengan kode `ERR_GW_TIMEOUT_504` namun dana tercatat berhasil "
            "terdebet di laporan bank mitra:\n"
            "   a) Untuk kanal Virtual Account & QRIS: Sistem melakukan pembaruan status menjadi SUCCESS (force-settlement) "
            "dan mengirimkan callback notifikasi lanjutan ke merchant.\n"
            "   b) Jika pesanan di sisi merchant sudah kadaluarsa, dana dimasukkan ke antrean Auto-Refund H+1 ke rekening nasabah.\n"
            "3. Seluruh selisih rekonsiliasi wajib diselesaikan maksimal dalam 2x24 jam kerja."
        ),
        (
            "9. DAFTAR KONTAK ESKALASI DAN RIWAYAT REVISI DOKUMEN\n\n"
            "- Desk Operasional 24/7: ops-payment@perusahaan.co.id (Ext. 4100)\n"
            "- Tim SRE & Gateway Infrastructure: sre-oncall@perusahaan.co.id (Ext. 4200)\n"
            "- Eskalasi Mitra Bank Nusantara: noc-gateway@banknusantara.co.id\n"
            "- Eskalasi Mitra Bank Mandiri Jaya: support-api@mandirijaya.co.id\n\n"
            "Riwayat Revisi:\n"
            "- Versi 4.0 (Januari 2026): Penambahan prosedur QRIS Dynamic.\n"
            "- Versi 4.2 (September 2026): Pembaruan aturan idempotency retry untuk ERR_GW_TIMEOUT_504 pada kanal API Partner dan Virtual Account."
        ),
    ]

    flowchart_png = _build_escalation_flowchart_png()

    for idx, text_body in enumerate(pages_content, start=1):
        page = pdf.new_page(width=595, height=842)  # A4
        header = f"SOP-OPS-04: Penanganan Transaksi Gagal | Halaman {idx} dari {len(pages_content)}"
        page.insert_text((45, 36), header, fontsize=8.5, color=(0.4, 0.4, 0.4))
        rect = fitz.Rect(45, 58, 550, 780)
        page.insert_textbox(rect, text_body, fontsize=10.5, color=(0.08, 0.08, 0.08))

        if idx == 4:
            # Embed flowchart image below the intro text on page 4
            img_rect = fitz.Rect(45, 160, 550, 520)
            page.insert_image(img_rect, stream=flowchart_png)
            caption = (
                "Gambar 4.1: Diagram Alur Klasifikasi Error (ERR_GW_TIMEOUT_504, ERR_INSUFFICIENT_FUNDS, "
                "ERR_INVALID_OTP) dan Eskalasi Rekonsiliasi H+1."
            )
            page.insert_textbox(fitz.Rect(45, 530, 550, 580), caption, fontsize=9, color=(0.25, 0.25, 0.25))

    pdf.save(str(pdf_path))
    pdf.close()
    print(f"[OK] Generated 8-page PDF with diagram: {pdf_path}")

    # 2. Laporan_Insiden_Oktober_2026.docx
    docx_path = SEED_DOCS_DIR / "Laporan_Insiden_Oktober_2026.docx"
    doc = DocxDocument()
    doc.add_heading("LAPORAN INVESTIGASI INSIDEN OPERASIONAL (INC-2026-1012)", level=1)
    doc.add_paragraph(
        "Judul Insiden: Lonjakan Kegagalan Transaksi (ERR_GW_TIMEOUT_504) pada Kanal API Partner dan Virtual Account Pasca Migrasi Gateway Bank Nusantara\n"
        "Tanggal Mulai Kejadian: 12 Oktober 2026 pukul 00:15 WIB\n"
        "Tanggal Deteksi & Investigasi: 12 Oktober 2026 - 31 Oktober 2026\n"
        "Tingkat Keparahan: P1 - High Operational Impact\n"
        "Status: Teridentifikasi & Dalam Mitigasi Bertahap"
    )

    doc.add_heading("1. Ringkasan Eksekutif (Executive Summary)", level=2)
    doc.add_paragraph(
        "Sejak tanggal 12 Oktober 2026, terjadi peningkatan signifikan pada tingkat kegagalan transaksi (failure rate) "
        "secara keseluruhan dibandingkan bulan September 2026 (naik dari baseline ~3,1% menjadi hampir ~8,9% di bulan Oktober 2026). "
        "Hasil investigasi tim Payment Operations dan SRE menunjukkan bahwa lonjakan kegagalan ini TIDAK terjadi secara merata di seluruh sistem, "
        "melainkan terkonsentrasi secara spesifik pada dua kanal: API Partner dan Virtual Account yang diproses melalui gateway Bank Nusantara."
    )

    doc.add_heading("2. Akar Permasalahan (Root Cause Analysis)", level=2)
    doc.add_paragraph(
        "Pada tanggal 11 Oktober 2026 pukul 23:30 WIB, mitra Bank Nusantara melakukan pembaruan infrastruktur Core Gateway v4.2 (rilis BN-GW-4.2). "
        "Pembaruan tersebut membawa dua perubahan parameter teknis yang berdampak langsung sejak 12 Oktober 2026 dini hari:\n"
        "1) Penurunan Batas Read-Timeout Sinkron: Konektor gateway menurunkan batas waktu tunggu respons (read timeout) dari 30.000 ms (30 detik) "
        "menjadi 10.000 ms (10 detik). Ketika proses validasi callback pada kanal API Partner dan pembuatan/verifikasi Virtual Account membutuhkan waktu "
        "di atas 9,5 detik pada jam sibuk, koneksi langsung diputus dengan kode error ERR_GW_TIMEOUT_504.\n"
        "2) Pengetatan Rate-Limiter Burst: Endpoint Host-to-Host Bank Nusantara menerapkan pembatasan kuota konkurensi baru pada trafik API Partner "
        "dan Virtual Account, sehingga antrean request memanjang dan waktu respons (response_time_ms) melonjak drastis dari rata-rata ~420 ms menjadi ~9.800 ms."
    )

    doc.add_heading("3. Dampak Kuantitatif per Kanal dan Kode Error", level=2)
    table = doc.add_table(rows=1, cols=5)
    hdr = table.rows[0].cells
    hdr[0].text = "Kanal Pembayaran"
    hdr[1].text = "Bank Terdampak"
    hdr[2].text = "Kode Error Dominan"
    hdr[3].text = "Failure Rate Sebelum 12 Okt"
    hdr[4].text = "Failure Rate Sejak 12 Okt (Bank Nusantara)"

    impact_rows = [
        ("API Partner", "Bank Nusantara", "ERR_GW_TIMEOUT_504", "~3.1%", "~34.0% (Total kanal ~14-16%)"),
        ("Virtual Account", "Bank Nusantara", "ERR_GW_TIMEOUT_504", "~3.1%", "~29.0% (Total kanal ~13-15%)"),
        ("QRIS / Mobile App / Web", "Semua Bank", "ERR_INSUFFICIENT_FUNDS (Normal)", "~3.1%", "~3.1% (Tidak Terdampak)"),
    ]
    for r in impact_rows:
        row_cells = table.add_row().cells
        for i, val in enumerate(r):
            row_cells[i].text = val

    doc.add_heading("4. Tindakan Mitigasi dan Rekomendasi Sesuai SOP-OPS-04", level=2)
    doc.add_paragraph(
        "Mengacu pada dokumen SOP_Penanganan_Transaksi_Gagal.pdf (SOP-OPS-04 Bab 4 & Bab 7):\n"
        "- Tim SRE menaikkan kembali konfigurasi timeout konektor internal dan mengaktifkan mekanisme Inquiry Status sebelum auto-retry maksimal 2 kali dengan idempotency key.\n"
        "- Mengalihkan sebagian beban trafik (load balancing reroute) Virtual Account dan API Partner dari Bank Nusantara ke Bank Mandiri Jaya dan Bank Sentral Niaga.\n"
        "- Menjalankan rekonsiliasi settlement H+1 secara ketat untuk memastikan tidak ada transaksi ERR_GW_TIMEOUT_504 yang terdebet ganda."
    )
    doc.save(str(docx_path))
    print(f"[OK] Generated DOCX incident report: {docx_path}")

    # 3. Kebijakan_Retry_dan_Rekonsiliasi_Operasional.txt (> 13,500 chars)
    txt_path = SEED_DOCS_DIR / "Kebijakan_Retry_dan_Rekonsiliasi_Operasional.txt"
    sections: list[str] = [
        "DOKUMEN KEBIJAKAN OPERASIONAL: POL-FIN-2026-09\n"
        "PEDOMAN TEKNIS AUTO-RETRY, REKONSILIASI SETTLEMENT, DAN KOMPENSASI SLA MERCHANT\n"
        "================================================================================\n"
    ]
    base_chapters = [
        (
            "BAB 1: KEBIJAKAN IDEMPOTENCY DAN AUTO-RETRY PADA GATEWAY PEMBAYARAN\n"
            "Setiap transaksi yang masuk melalui kanal API Partner, Virtual Account, Web, Mobile App, maupun QRIS wajib memiliki "
            "identitas unik `tx_id` dan `idempotency_key`. Apabila transaksi gagal dengan kode `ERR_GW_TIMEOUT_504`, sistem tidak diperbolehkan "
            "membuat transaksi baru tanpa melakukan pengecekan status (`status_inquiry`) ke bank mitra terlebih dahulu. "
            "Jeda waktu antar percobaan ulang (retry backoff) ditetapkan sebesar 2 detik pada percobaan pertama dan 5 detik pada percobaan kedua. "
            "Untuk kegagalan bisnis seperti `ERR_INSUFFICIENT_FUNDS`, `ERR_LIMIT_EXCEEDED`, dan `ERR_INVALID_OTP`, mekanisme auto-retry "
            "dilarang keras karena melanggar ketentuan kepatuhan jaringan perbankan dan meningkatkan biaya penalti API."
        ),
        (
            "BAB 2: STANDAR WAKTU RESPONS (LATENCY) DAN PEMANTAUAN ANOMALI\n"
            "Dalam kondisi operasional normal, waktu pemrosesan transaksi (`response_time_ms`) berada pada kisaran 180 ms hingga 700 ms. "
            "Apabila rata-rata waktu respons pada suatu bank gateway melampaui 2.500 ms, sistem peringatan dini akan mengirimkan notifikasi "
            "ke kanal komunikasi L2 Payment Operations. Jika waktu respons mendekati 9.000 ms - 10.000 ms disertai lonjakan kode `ERR_GW_TIMEOUT_504`, "
            "hal tersebut mengindikasikan adanya pemutusan paksa oleh parameter read-timeout di sisi gateway atau antrean koneksi yang jenuh."
        ),
        (
            "BAB 3: PROSEDUR REKONSILIASI DANA DAN PENANGANAN TRANSAKSI SUSPECT\n"
            "Rekonsiliasi dilakukan secara otomatis setiap hari pada pukul 06:00 WIB untuk seluruh transaksi hari sebelumnya (H+1). "
            "Transaksi yang tercatat FAILED karena `ERR_GW_TIMEOUT_504` maupun berstatus `PENDING` lebih dari 60 menit dikategorikan sebagai "
            "transaksi perlu verifikasi mutasi. Apabila dana terbukti masuk ke rekening penampungan (escrow) berdasarkan laporan settlement bank mitra, "
            "tim Finance Operations wajib melakukan pembaruan status atau pengembalian dana (refund) sesuai instruksi merchant maksimal dalam 48 jam."
        ),
        (
            "BAB 4: KOMPENSASI SLA DAN KOMUNIKASI KEPADA MERCHANT ENTERPRISE\n"
            "Merchant tier Enterprise yang menggunakan kanal API Partner dan Virtual Account memiliki jaminan tingkat keberhasilan (Success Rate SLA) "
            "minimal 96,0% di luar kegagalan sisi nasabah (`ERR_INSUFFICIENT_FUNDS`). Apabila terjadi insiden infrastruktur pada salah satu bank gateway "
            "seperti insiden migrasi Bank Nusantara pada Oktober 2026, Account Manager wajib mengirimkan buletin status resmi beserta rencana "
            "pengalihan rute (traffic rerouting) kepada seluruh merchant terdampak."
        ),
    ]
    # Expand detailed operational clauses so file is > 13,500 chars
    for ch_idx in range(1, 14):
        chapter_text = base_chapters[(ch_idx - 1) % len(base_chapters)]
        sections.append(
            f"\n--- BAGIAN LAMPIRAN TEKNIS {ch_idx:02d} ---\n"
            f"{chapter_text}\n"
            f"Klausul Operasional Tambahan {ch_idx}.1: Tim operasional wajib mendokumentasikan setiap anomali kanal, kategori merchant, "
            f"dan distribusi kode error per bulan ke dalam repositori audit internal.\n"
            f"Klausul Operasional Tambahan {ch_idx}.2: Seluruh perubahan parameter timeout pada bank mitra (termasuk Bank Nusantara, "
            f"Bank Mandiri Jaya, Bank Sentral Niaga, Bank Prima Digital, dan WalletPay Gateway) harus melewati uji beban (load testing) "
            f"sebelum diterapkan pada trafik produksi kanal API Partner dan Virtual Account.\n"
        )

    full_txt = "\n".join(sections)
    txt_path.write_text(full_txt, encoding="utf-8")
    print(f"[OK] Generated policy TXT ({len(full_txt):,} chars): {txt_path}")


def generate_sample_files() -> None:
    """Create sample spreadsheets and short documents in sample_files/ for demo uploads."""
    SAMPLE_FILES_DIR.mkdir(parents=True, exist_ok=True)

    # 1. target_kpi_2026.xlsx (2 sheets)
    xlsx_path = SAMPLE_FILES_DIR / "target_kpi_2026.xlsx"
    df_monthly = pd.DataFrame(
        [
            {"tx_month": "2026-05", "target_total_tx": 14000, "target_gmv_idr": 12_500_000_000, "target_success_rate_pct": 95.5, "max_failure_rate_pct": 3.5},
            {"tx_month": "2026-06", "target_total_tx": 14400, "target_gmv_idr": 13_000_000_000, "target_success_rate_pct": 95.5, "max_failure_rate_pct": 3.5},
            {"tx_month": "2026-07", "target_total_tx": 15500, "target_gmv_idr": 14_200_000_000, "target_success_rate_pct": 95.5, "max_failure_rate_pct": 3.5},
            {"tx_month": "2026-08", "target_total_tx": 16000, "target_gmv_idr": 14_500_000_000, "target_success_rate_pct": 95.5, "max_failure_rate_pct": 3.5},
            {"tx_month": "2026-09", "target_total_tx": 16500, "target_gmv_idr": 15_000_000_000, "target_success_rate_pct": 96.0, "max_failure_rate_pct": 3.2},
            {"tx_month": "2026-10", "target_total_tx": 17500, "target_gmv_idr": 16_000_000_000, "target_success_rate_pct": 96.0, "max_failure_rate_pct": 3.2},
        ]
    )
    df_channel = pd.DataFrame(
        [
            {"channel": "QRIS", "target_oct_tx": 5200, "target_success_rate_pct": 96.5, "max_latency_ms": 500},
            {"channel": "Mobile App", "target_oct_tx": 3900, "target_success_rate_pct": 96.0, "max_latency_ms": 600},
            {"channel": "Virtual Account", "target_oct_tx": 3200, "target_success_rate_pct": 96.0, "max_latency_ms": 800},
            {"channel": "API Partner", "target_oct_tx": 3000, "target_success_rate_pct": 96.5, "max_latency_ms": 750},
            {"channel": "Web", "target_oct_tx": 2200, "target_success_rate_pct": 95.5, "max_latency_ms": 700},
        ]
    )
    with pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:
        df_monthly.to_excel(writer, sheet_name="Target_Bulanan", index=False)
        df_channel.to_excel(writer, sheet_name="Target_Per_Kanal", index=False)
    print(f"[OK] Generated sample Excel: {xlsx_path}")

    # 2. biaya_mdr_per_kanal.csv
    csv_path = SAMPLE_FILES_DIR / "biaya_mdr_per_kanal.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["channel", "mdr_fee_pct", "fixed_fee_idr", "gateway_cost_idr", "settlement_sla"])
        w.writerow(["QRIS", 0.70, 0, 150, "H+1"])
        w.writerow(["Mobile App", 1.20, 1000, 350, "H+1"])
        w.writerow(["Web", 1.50, 1500, 450, "H+1"])
        w.writerow(["Virtual Account", 0.00, 4000, 1200, "Real-time / H+1"])
        w.writerow(["API Partner", 0.00, 3500, 1000, "Real-time"])
    print(f"[OK] Generated sample CSV: {csv_path}")

    # 3. memo_ops_singkat.txt (< 12,000 chars)
    memo_path = SAMPLE_FILES_DIR / "memo_ops_singkat.txt"
    memo_path.write_text(
        "MEMO INTERNAL TIM OPERASIONAL — 25 OKTOBER 2026\n"
        "Perihal: Evaluasi Kampanye Promo Merchant F&B dan Pemantauan Gateway Oktober\n\n"
        "1. Program cashback QRIS pada merchant kategori F&B yang dimulai sejak September 2026 berhasil mendorong "
        "kenaikan volume transaksi F&B sebesar lebih dari 20% dibandingkan pertengahan tahun.\n"
        "2. Sebaliknya, kategori Travel mengalami penurunan musiman sejak Agustus 2026 setelah berakhirnya periode liburan sekolah.\n"
        "3. Tim Ops diminta memantau ketat koneksi Bank Nusantara pada kanal API Partner dan Virtual Account karena keluhan timeout "
        "sejak tanggal 12 Oktober 2026.\n",
        encoding="utf-8",
    )
    print(f"[OK] Generated short TXT memo: {memo_path}")


def main() -> None:
    generate_sqlite_database()
    generate_seed_documents()
    generate_sample_files()


if __name__ == "__main__":
    main()
