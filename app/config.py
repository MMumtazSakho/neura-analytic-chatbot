"""Central configuration for the AI-Powered Data Analytics Chatbot."""

from __future__ import annotations

import math
import os
from pathlib import Path
import certifi
from dotenv import load_dotenv

os.environ.setdefault("SSL_CERT_FILE", certifi.where())
os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())

ROOT = Path(__file__).resolve().parent.parent
WORKSPACE_ROOT = ROOT.parent

# Load .env from workspace root (neura/.env) as well as analytics_chatbot/.env if present
load_dotenv(WORKSPACE_ROOT / ".env")
load_dotenv(ROOT / ".env")

APP_NAME = "neura_analytics_chatbot"
DEFAULT_USER_ID = "analyst-user"

# OpenRouter Models & Endpoints
LLM_MODEL = os.getenv("LLM_MODEL", "openrouter/z-ai/glm-5.3-flash")
EMBED_MODEL = os.getenv("EMBED_MODEL", "google/gemini-embedding-2")
EMBED_DIM = int(os.getenv("EMBED_DIM", "1536"))
OPENROUTER_BASE = os.getenv("OPENROUTER_BASE", "https://openrouter.ai/api/v1")


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name, str(default)).strip().lower()
    if value in {"true", "1", "yes"}:
        return True
    if value in {"false", "0", "no"}:
        return False
    raise ValueError(f"{name} harus true/false, 1/0, atau yes/no.")


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} harus integer >= {minimum}.") from exc
    if value < minimum:
        raise ValueError(f"{name} harus integer >= {minimum}.")
    return value


RERANK_ENABLED = _env_bool("RERANK_ENABLED")
RERANK_MODEL = os.getenv("RERANK_MODEL", "qwen/qwen3-reranker-8b").strip()
try:
    RERANK_TIMEOUT_SECONDS = float(os.getenv("RERANK_TIMEOUT_SECONDS", "8"))
except ValueError as exc:
    raise ValueError("RERANK_TIMEOUT_SECONDS harus angka positif dan finite.") from exc
if not math.isfinite(RERANK_TIMEOUT_SECONDS) or RERANK_TIMEOUT_SECONDS <= 0:
    raise ValueError("RERANK_TIMEOUT_SECONDS harus angka positif dan finite.")
if not RERANK_MODEL:
    raise ValueError("RERANK_MODEL tidak boleh kosong.")
RERANK_TEXT_CANDIDATES = _env_int("RERANK_TEXT_CANDIDATES", 20)
RERANK_IMAGE_TOP_K = _env_int("RERANK_IMAGE_TOP_K", 2, minimum=0)
RERANK_FINAL_TOP_K = _env_int("RERANK_FINAL_TOP_K", 6)

# Thresholds & Guardrails
PDF_PAGE_LIMIT = 5
TEXT_CHAR_LIMIT = 12_000
SQL_ROW_LIMIT = 200
SQL_TIMEOUT_SECONDS = 5.0
PY_EXEC_TIMEOUT = 60
PY_OUTPUT_LIMIT = 8_000
KERNEL_IDLE_TTL = 20 * 60
MAX_LLM_CALLS = 25

# Data & Runtime Directories
DATA_DIR = ROOT / "data"
SAMPLE_FILES_DIR = ROOT / "sample_files"
STATIC_DIR = ROOT / "static"

TRANSACTIONS_DB_PATH = DATA_DIR / "transactions.db"
APP_STATE_DB_PATH = DATA_DIR / "app_state.db"
CHROMA_DIR = DATA_DIR / "chroma"
KB_FILES_DIR = DATA_DIR / "kb_files"
KB_IMAGES_DIR = DATA_DIR / "kb_images"
SHARED_FILES_DIR = DATA_DIR / "shared_files"
WORKSPACES_DIR = DATA_DIR / "workspaces"


def ensure_runtime_dirs() -> None:
    """Create all runtime directories if they do not exist."""
    for d in (
        DATA_DIR,
        SAMPLE_FILES_DIR,
        CHROMA_DIR,
        KB_FILES_DIR,
        KB_IMAGES_DIR,
        SHARED_FILES_DIR,
        WORKSPACES_DIR,
    ):
        d.mkdir(parents=True, exist_ok=True)


def get_openrouter_api_key() -> str:
    """Return the configured OpenRouter API key or raise RuntimeError."""
    key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "OPENROUTER_API_KEY belum dikonfigurasi di file .env."
        )
    # LiteLlm reads OPENROUTER_API_KEY from environment
    os.environ["OPENROUTER_API_KEY"] = key
    return key
