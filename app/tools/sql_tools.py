"""NL2SQL tools with enriched schema discovery and read-only guardrails."""

from __future__ import annotations

import csv
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

from google.adk.tools.tool_context import ToolContext

from ..config import (
    DATA_DIR,
    SQL_EXPORT_ROW_LIMIT,
    SQL_PREVIEW_ROW_LIMIT,
    SQL_ROW_LIMIT,
    SQL_TIMEOUT_SECONDS,
    TRANSACTIONS_DB_PATH,
)
from ..sandbox.kernel_pool import ensure_session_workspace

TABLE_DESCRIPTIONS: dict[str, str] = {
    "sales_orders": (
        "Data kontrak penjualan dan langganan produk/layanan PT Neural Technologies Indonesia (1 Mei 2026 - 31 Oktober 2026). "
        "Mencakup produk JustClick ERP (HCMS, eFinance, CRM, POS, SMART), Custom ERP, IT Staff Augmentation, dan Corporate Training."
    ),
    "clients": (
        "Data profil klien korporat B2B yang menggunakan software & solusi IT dari PT Neural Technologies Indonesia."
    ),
    "transactions": (
        "View kompatibilitas yang merujuk ke tabel sales_orders."
    ),
    "merchants": (
        "View kompatibilitas yang merujuk ke tabel clients."
    ),
}

COLUMN_DESCRIPTIONS: dict[tuple[str, str], str] = {
    ("sales_orders", "order_id"): "ID unik kontrak/pesanan penjualan (contoh: SO2026000001)",
    ("sales_orders", "created_at"): "Timestamp pesanan/kontrak dalam format ISO YYYY-MM-DD HH:MM:SS",
    ("sales_orders", "order_date"): "Tanggal pesanan dalam format YYYY-MM-DD",
    ("sales_orders", "order_month"): "Bulan pesanan dalam format YYYY-MM (contoh: 2026-10)",
    ("sales_orders", "client_id"): "Foreign key ke tabel clients.client_id",
    ("sales_orders", "client_name"): "Nama resmi perusahaan klien B2B",
    ("sales_orders", "industry"): "Sektor industri klien (Manufacturing, Plantation & Agro, Education, Healthcare, Financial Services, Retail & Distribution, Gov & BUMD, Mining & Energy)",
    ("sales_orders", "product_line"): "Lini produk/layanan NTI (JustClick HCMS, JustClick eFinance, JustClick CRM, JustClick Sales & POS, JustClick SMART, ERP Custom Implementation, IT Staff Augmentation, Corporate Training)",
    ("sales_orders", "license_type"): "Model lisensi: SaaS Monthly, SaaS Annual, On-Premise Perpetual, atau Professional Services",
    ("sales_orders", "user_count"): "Jumlah user license / seat / kasir yang dilanggani",
    ("sales_orders", "amount"): "Nilai transaksi/kontrak penjualan dalam Rupiah (IDR)",
    ("sales_orders", "status"): "Status kontrak: WON, ACTIVE, RENEWED, CHURNED, PENDING_PAYMENT, atau CANCELLED",
    ("sales_orders", "loss_reason"): "Alasan pembatalan/churn jika status=CHURNED/CANCELLED (contoh: ERR_PAYROLL_CUSTOM_DELAY, BUDGET_REALLOCATION, COMPETITOR_PRICE)",
    ("sales_orders", "deployment_days"): "Waktu implementasi / deployment hingga go-live dalam hari",
    ("sales_orders", "sales_rep"): "Account Executive / Sales Representative penanggung jawab",
    ("clients", "client_id"): "ID unik klien (contoh: C0001)",
    ("clients", "name"): "Nama resmi perusahaan klien B2B",
    ("clients", "industry"): "Sektor industri klien",
    ("clients", "city"): "Kota domisili operasional klien",
    ("clients", "tier"): "Kategori skala klien (Enterprise, Mid-Market, SMB)",
    ("clients", "account_manager"): "Account Manager pendamping dari tim NTI",
    ("clients", "onboarded_at"): "Tanggal pertama kali klien bergabung (YYYY-MM-DD)",
}

_SCHEMA_CACHE: dict[str, Any] | None = None

_ALLOWED_AUTHORIZER_ACTIONS = {
    sqlite3.SQLITE_SELECT,
    sqlite3.SQLITE_READ,
    sqlite3.SQLITE_FUNCTION,
}
if hasattr(sqlite3, "SQLITE_RECURSIVE"):
    _ALLOWED_AUTHORIZER_ACTIONS.add(sqlite3.SQLITE_RECURSIVE)


def _read_only_authorizer(
    action: int,
    arg1: str | None,
    arg2: str | None,
    dbname: str | None,
    source: str | None,
) -> int:
    if action in _ALLOWED_AUTHORIZER_ACTIONS:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_PRAGMA and arg1 and arg1.lower() in {"table_info", "index_list"}:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def _open_readonly_connection() -> sqlite3.Connection:
    if not TRANSACTIONS_DB_PATH.exists():
        raise FileNotFoundError(
            f"Database belum tersedia di {TRANSACTIONS_DB_PATH}. Jalankan scripts/generate_data.py terlebih dahulu."
        )
    uri = f"file:{TRANSACTIONS_DB_PATH.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=SQL_TIMEOUT_SECONDS)
    conn.row_factory = sqlite3.Row
    conn.set_authorizer(_read_only_authorizer)
    return conn


def invalidate_schema_cache() -> None:
    global _SCHEMA_CACHE
    _SCHEMA_CACHE = None


def get_database_schema() -> dict[str, Any]:
    """Inspect the operational SQL database schema, column meanings, date ranges, and distinct categorical values.

    Call this tool first before writing SQL queries so you know the exact table names,
    column names, valid categorical values (channels, statuses, error_codes, banks, categories),
    and the date range covered by the dataset.
    """
    global _SCHEMA_CACHE
    if _SCHEMA_CACHE is not None:
        return _SCHEMA_CACHE

    try:
        conn = _open_readonly_connection()
    except Exception as exc:
        return {"error": str(exc)}

    try:
        cursor = conn.cursor()
        tables_rows = cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()

        tables_info: list[dict[str, Any]] = []
        for row in tables_rows:
            tname = row["name"]
            count_row = cursor.execute(f'SELECT COUNT(*) AS cnt FROM "{tname}"').fetchone()
            row_count = count_row["cnt"] if count_row else 0

            col_rows = cursor.execute(f'PRAGMA table_info("{tname}")').fetchall()
            columns: list[dict[str, Any]] = []

            for col in col_rows:
                cname = col["name"]
                ctype = col["type"]
                col_entry: dict[str, Any] = {
                    "name": cname,
                    "type": ctype,
                    "description": COLUMN_DESCRIPTIONS.get((tname, cname), ""),
                }

                # Check if categorical or date column to enrich context
                if cname in {
                    "product_line",
                    "license_type",
                    "industry",
                    "status",
                    "loss_reason",
                    "tier",
                    "city",
                    "sales_rep",
                    "account_manager",
                    "order_month",
                    "channel",
                    "payment_method",
                    "bank",
                    "merchant_category",
                    "error_code",
                    "tx_month",
                }:
                    distinct_rows = cursor.execute(
                        f'SELECT DISTINCT "{cname}" AS val FROM "{tname}" WHERE "{cname}" IS NOT NULL ORDER BY "{cname}" LIMIT 31'
                    ).fetchall()
                    vals = [r["val"] for r in distinct_rows]
                    if len(vals) <= 30:
                        col_entry["distinct_values"] = vals

                if cname in {"created_at", "order_date", "tx_date", "onboarded_at"}:
                    minmax = cursor.execute(
                        f'SELECT MIN("{cname}") AS min_val, MAX("{cname}") AS max_val FROM "{tname}"'
                    ).fetchone()
                    if minmax:
                        col_entry["min_value"] = minmax["min_val"]
                        col_entry["max_value"] = minmax["max_val"]

                columns.append(col_entry)

            tables_info.append(
                {
                    "table_name": tname,
                    "description": TABLE_DESCRIPTIONS.get(tname, ""),
                    "row_count": row_count,
                    "columns": columns,
                }
            )

        schema_data = {
            "database_engine": "SQLite (Read-Only)",
            "reference_note": (
                "Gunakan tanggal maksimum pada tabel sales_orders (2026-10-31) sebagai acuan 'hari ini' / 'bulan ini' (Oktober 2026), "
                "dan September 2026 ('2026-09') sebagai 'bulan sebelumnya'."
            ),
            "tables": tables_info,
        }
        _SCHEMA_CACHE = schema_data
        return schema_data
    except Exception as exc:
        return {"error": f"Gagal membaca skema database: {exc}"}
    finally:
        conn.close()


def _validate_select_sql(sql: str) -> str | None:
    cleaned = sql.strip()
    if not cleaned:
        return "Query SQL tidak boleh kosong."

    # Strip trailing semicolon
    if cleaned.endswith(";"):
        cleaned = cleaned[:-1].strip()

    # Disallow multiple statements
    if ";" in cleaned:
        return "Hanya satu statement SELECT atau WITH yang diizinkan per pemanggilan."

    first_token_match = re.match(r"^\s*([a-zA-Z]+)", cleaned)
    if not first_token_match:
        return "Query SQL tidak valid."

    first_word = first_token_match.group(1).upper()
    if first_word not in {"SELECT", "WITH"}:
        return (
            f"Operasi '{first_word}' ditolak. Database bersifat read-only; "
            "hanya query SELECT atau WITH (CTE) yang diizinkan."
        )

    forbidden_pattern = re.compile(
        r"\b(DROP|DELETE|UPDATE|INSERT|ALTER|CREATE|REPLACE|ATTACH|DETACH|PRAGMA|VACUUM)\b",
        re.IGNORECASE,
    )
    match = forbidden_pattern.search(cleaned)
    if match:
        return f"Keyword '{match.group(1).upper()}' tidak diizinkan pada mode read-only."

    return None


def _save_rows_to_workspace_csv(
    session_id: str,
    columns: list[str],
    rows: list[Any],
) -> str:
    """Save SQL query rows as a CSV file inside the session's Python workspace."""
    ws = ensure_session_workspace(session_id)
    sql_dir = ws / "sql_results"
    sql_dir.mkdir(parents=True, exist_ok=True)

    existing_nums = [
        int(m.group(1))
        for p in sql_dir.glob("query_*.csv")
        if (m := re.match(r"^query_(\d+)\.csv$", p.name))
    ]
    next_idx = (max(existing_nums) + 1) if existing_nums else 1
    filename = f"query_{next_idx}.csv"
    out_path = sql_dir / filename

    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(columns)
        for r in rows:
            writer.writerow([r[col] for col in columns])

    return f"sql_results/{filename}"


def _parse_bool_flag(val: Any) -> bool:
    if isinstance(val, bool):
        return val
    if val is None:
        return False
    return str(val).strip().lower() in {"true", "1", "yes"}


def run_sql_query(
    sql: str,
    save: bool = False,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Execute a read-only SQLite SELECT query on the operational database.

    Always prefer aggregating in SQL (COUNT, SUM, AVG, GROUP BY, CASE WHEN) rather than
    fetching raw rows.
    - Set `save=True` whenever you plan to visualize the SQL result or analyze it further in
      `execute_python`. The full query result will be saved to a CSV file in the Python workspace
      (e.g. `sql_results/query_1.csv`) so you can load it directly with `pd.read_csv(saved_path)`
      without re-typing any data.
    - Even when `save=False`, if the query returns more than 50 rows, the tool automatically saves
      the full result to a CSV file in `sql_results/` and returns a 10-row preview + `saved_path`
      so you can continue processing in `execute_python`.

    Args:
        sql: A single SQLite SELECT or WITH query string.
        save: Set True to save the query result as a CSV file in the Python workspace (`sql_results/query_<n>.csv`) for direct loading via `pd.read_csv(...)` in `execute_python`.
    """
    validation_error = _validate_select_sql(sql)
    if validation_error:
        return {
            "error": validation_error,
            "sql": sql,
            "hint": "Gunakan statement SELECT tunggal pada tabel sales_orders atau clients.",
        }

    try:
        conn = _open_readonly_connection()
    except Exception as exc:
        return {"error": str(exc), "sql": sql}

    started = time.perf_counter()

    def _progress_timeout() -> int:
        if (time.perf_counter() - started) > SQL_TIMEOUT_SECONDS:
            return 1  # abort query
        return 0

    conn.set_progress_handler(_progress_timeout, 10_000)
    should_save = _parse_bool_flag(save)
    session = getattr(tool_context, "session", None) if tool_context else None
    session_id = getattr(session, "id", None) or "default"

    try:
        cursor = conn.cursor()
        cleaned_sql = sql.strip().rstrip(";")
        cursor.execute(cleaned_sql)
        columns = [desc[0] for desc in (cursor.description or [])]

        initial_fetched = cursor.fetchmany(SQL_ROW_LIMIT + 1)
        exceeds_inline_limit = len(initial_fetched) > SQL_ROW_LIMIT

        if should_save or exceeds_inline_limit:
            remaining_quota = max(0, (SQL_EXPORT_ROW_LIMIT + 1) - len(initial_fetched))
            more_fetched = cursor.fetchmany(remaining_quota) if remaining_quota > 0 else []
            all_fetched = initial_fetched + more_fetched
            export_truncated = len(all_fetched) > SQL_EXPORT_ROW_LIMIT
            rows_to_save = all_fetched[:SQL_EXPORT_ROW_LIMIT]

            saved_path = _save_rows_to_workspace_csv(
                session_id=session_id,
                columns=columns,
                rows=rows_to_save,
            )
            preview_slice = rows_to_save[:SQL_PREVIEW_ROW_LIMIT]
            preview_records = [dict(zip(columns, row)) for row in preview_slice]
            elapsed_ms = round((time.perf_counter() - started) * 1000, 1)

            reason_msg = (
                f"Hasil query memiliki {len(rows_to_save)} baris (melebihi batas inline {SQL_ROW_LIMIT} baris)"
                if (exceeds_inline_limit and not should_save)
                else f"Hasil query ({len(rows_to_save)} baris) disimpan karena save=True"
            )
            note = (
                f"{reason_msg}. Seluruh data telah disimpan ke '{saved_path}'. "
                f"Hanya {len(preview_records)} baris pertama yang ditampilkan pada preview_rows agar konteks tetap ringkas. "
                f"Jangan mengetik ulang data secara manual; lanjutkan di tool execute_python dengan membaca file: "
                f"df = pd.read_csv('{saved_path}')"
            )
            if export_truncated:
                note += f" (Catatan: file CSV dipotong pada batas maksimum {SQL_EXPORT_ROW_LIMIT} baris)."

            return {
                "sql": cleaned_sql,
                "columns": columns,
                "row_count": len(rows_to_save),
                "saved": True,
                "auto_saved": bool(exceeds_inline_limit and not should_save),
                "saved_path": saved_path,
                "preview_rows": preview_records,
                "preview_count": len(preview_records),
                "truncated": export_truncated,
                "elapsed_ms": elapsed_ms,
                "note": note,
            }

        records = [dict(zip(columns, row)) for row in initial_fetched]
        elapsed_ms = round((time.perf_counter() - started) * 1000, 1)

        return {
            "sql": cleaned_sql,
            "columns": columns,
            "rows": records,
            "row_count": len(records),
            "saved": False,
            "truncated": False,
            "elapsed_ms": elapsed_ms,
        }
    except sqlite3.OperationalError as exc:
        msg = str(exc)
        if "interrupted" in msg.lower():
            return {
                "error": f"Query melebihi batas waktu ({SQL_TIMEOUT_SECONDS} detik).",
                "sql": sql,
                "hint": "Sederhanakan query atau tambahkan filter/agregasi.",
            }
        return {
            "error": f"SQLite error: {msg}",
            "sql": sql,
            "hint": "Periksa nama tabel/kolom melalui get_database_schema() dan perbaiki syntax SQLite.",
        }
    except sqlite3.DatabaseError as exc:
        return {
            "error": f"Database error: {exc}",
            "sql": sql,
            "hint": "Pastikan hanya menjalankan operasi baca (SELECT).",
        }
    finally:
        conn.close()


def _discover_db_files() -> list[Path]:
    """Return SQLite database files inside DATA_DIR excluding internal app_state.db."""
    found: list[Path] = []
    if TRANSACTIONS_DB_PATH.exists():
        found.append(TRANSACTIONS_DB_PATH.resolve())
    if DATA_DIR.exists():
        for p in sorted(DATA_DIR.iterdir()):
            if not p.is_file():
                continue
            if p.name == "app_state.db":
                continue
            if p.suffix.lower() in {".db", ".sqlite", ".sqlite3"}:
                resolved = p.resolve()
                if resolved not in found:
                    found.append(resolved)
    return found


def _resolve_allowed_db_path(db_id: str) -> Path:
    """Resolve a database identifier to an allowed read-only SQLite file path."""
    cleaned = db_id.strip()
    for candidate in _discover_db_files():
        if candidate.name == cleaned or candidate.stem == cleaned:
            return candidate
    raise FileNotFoundError(f"Database '{db_id}' tidak ditemukan.")


def _open_readonly_path(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{db_path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=SQL_TIMEOUT_SECONDS)
    conn.row_factory = sqlite3.Row
    conn.set_authorizer(_read_only_authorizer)
    return conn


def list_databases_info() -> list[dict[str, Any]]:
    """Return metadata and table schemas for all operational SQLite databases."""
    databases: list[dict[str, Any]] = []
    for db_path in _discover_db_files():
        is_primary = db_path == TRANSACTIONS_DB_PATH.resolve()
        size_bytes = db_path.stat().st_size if db_path.exists() else 0

        if is_primary:
            schema = get_database_schema()
            tables = schema.get("tables", []) if isinstance(schema, dict) else []
            reference_note = (
                schema.get("reference_note", "") if isinstance(schema, dict) else ""
            )
        else:
            tables = []
            reference_note = ""
            try:
                conn = _open_readonly_path(db_path)
                try:
                    cur = conn.cursor()
                    t_rows = cur.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
                    ).fetchall()
                    for tr in t_rows:
                        tname = tr["name"]
                        cnt_row = cur.execute(f'SELECT COUNT(*) AS cnt FROM "{tname}"').fetchone()
                        rcnt = cnt_row["cnt"] if cnt_row else 0
                        c_rows = cur.execute(f'PRAGMA table_info("{tname}")').fetchall()
                        cols = [
                            {
                                "name": cr["name"],
                                "type": cr["type"],
                                "description": COLUMN_DESCRIPTIONS.get((tname, cr["name"]), ""),
                            }
                            for cr in c_rows
                        ]
                        tables.append(
                            {
                                "table_name": tname,
                                "description": TABLE_DESCRIPTIONS.get(tname, ""),
                                "row_count": rcnt,
                                "columns": cols,
                            }
                        )
                finally:
                    conn.close()
            except Exception as exc:
                tables = [{"error": str(exc)}]

        total_rows = sum(
            int(t.get("row_count", 0)) for t in tables if isinstance(t, dict) and "row_count" in t
        )
        databases.append(
            {
                "id": db_path.name,
                "name": db_path.name,
                "label": (
                    "Operational Payment Gateway DB"
                    if is_primary
                    else db_path.stem.replace("_", " ").title()
                ),
                "path": f"data/{db_path.name}",
                "engine": "SQLite 3 (Read-Only)",
                "active_nl2sql": is_primary,
                "size_bytes": size_bytes,
                "table_count": len(tables),
                "total_rows": total_rows,
                "guardrails": {
                    "mode": "Read-Only (URI mode=ro + SQLite Authorizer)",
                    "row_limit": SQL_ROW_LIMIT,
                    "timeout_seconds": SQL_TIMEOUT_SECONDS,
                },
                "reference_note": reference_note,
                "tables": tables,
            }
        )
    return databases


def preview_database_table(
    db_id: str,
    table_name: str,
    limit: int = 25,
    offset: int = 0,
) -> dict[str, Any]:
    """Return a paginated preview of rows and column definitions for a specific table."""
    db_path = _resolve_allowed_db_path(db_id)
    clamped_limit = max(1, min(int(limit), 100))
    clamped_offset = max(0, int(offset))

    conn = _open_readonly_path(db_path)
    try:
        cur = conn.cursor()
        valid_tables = {
            r["name"]
            for r in cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }
        if table_name not in valid_tables:
            raise ValueError(f"Tabel '{table_name}' tidak ditemukan di database '{db_path.name}'.")

        cnt_row = cur.execute(f'SELECT COUNT(*) AS cnt FROM "{table_name}"').fetchone()
        total_rows = int(cnt_row["cnt"]) if cnt_row else 0

        # Enrich columns from cached schema if available
        cached_cols_map: dict[str, dict[str, Any]] = {}
        if db_path == TRANSACTIONS_DB_PATH.resolve():
            schema = get_database_schema()
            for t in schema.get("tables", []):
                if t.get("table_name") == table_name:
                    for c in t.get("columns", []):
                        cached_cols_map[c["name"]] = c

        col_rows = cur.execute(f'PRAGMA table_info("{table_name}")').fetchall()
        columns: list[dict[str, Any]] = []
        for col in col_rows:
            cname = col["name"]
            base_col: dict[str, Any] = {
                "name": cname,
                "type": col["type"],
                "pk": bool(col["pk"]),
                "notnull": bool(col["notnull"]),
                "description": COLUMN_DESCRIPTIONS.get((table_name, cname), ""),
            }
            if cname in cached_cols_map:
                for extra_key in ("distinct_values", "min_value", "max_value", "description"):
                    if extra_key in cached_cols_map[cname] and cached_cols_map[cname][extra_key]:
                        base_col[extra_key] = cached_cols_map[cname][extra_key]
            columns.append(base_col)

        data_rows = cur.execute(
            f'SELECT * FROM "{table_name}" LIMIT ? OFFSET ?',
            (clamped_limit, clamped_offset),
        ).fetchall()
        col_names = [c["name"] for c in columns]
        rows = [dict(zip(col_names, r)) for r in data_rows]

        return {
            "db_id": db_path.name,
            "table_name": table_name,
            "description": TABLE_DESCRIPTIONS.get(table_name, ""),
            "total_rows": total_rows,
            "limit": clamped_limit,
            "offset": clamped_offset,
            "columns": columns,
            "rows": rows,
        }
    finally:
        conn.close()

