"""Persistent per-session Jupyter kernel pool for stateful Python & Pandas analysis."""

from __future__ import annotations

import asyncio
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jupyter_client.manager import AsyncKernelManager

from ..config import (
    KERNEL_IDLE_TTL,
    PY_EXEC_TIMEOUT,
    PY_OUTPUT_LIMIT,
    SHARED_FILES_DIR,
    WORKSPACES_DIR,
)

ANSI_ESCAPE_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")

BOOTSTRAP_CODE = """\
import os, math, json
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import seaborn as sns

pd.set_option("display.max_columns", 30)
pd.set_option("display.max_rows", 60)
pd.set_option("display.width", 140)
pd.set_option("display.float_format", lambda x: f"{x:,.2f}")

plt.rcParams["figure.figsize"] = (9, 4.8)
plt.rcParams["figure.dpi"] = 130
plt.rcParams["axes.spines.top"] = False
plt.rcParams["axes.spines.right"] = False
plt.rcParams["axes.grid"] = True
plt.rcParams["grid.alpha"] = 0.25
plt.rcParams["font.size"] = 10
"""


def strip_ansi(text: str) -> str:
    return ANSI_ESCAPE_RE.sub("", text or "")


def ensure_session_workspace(session_id: str) -> Path:
    """Ensure session workspace structure (uploads/, outputs/, shared symlink) exists."""
    safe_sid = re.sub(r"[^a-zA-Z0-9_\-]", "_", session_id or "default")
    ws = (WORKSPACES_DIR / safe_sid).resolve()
    (ws / "uploads").mkdir(parents=True, exist_ok=True)
    (ws / "outputs").mkdir(parents=True, exist_ok=True)
    SHARED_FILES_DIR.mkdir(parents=True, exist_ok=True)

    shared_link = ws / "shared"
    if not shared_link.exists() and not shared_link.is_symlink():
        try:
            shared_link.symlink_to(SHARED_FILES_DIR.resolve(), target_is_directory=True)
        except OSError:
            pass
    return ws


@dataclass
class SessionKernel:
    session_id: str
    workspace: Path
    km: AsyncKernelManager
    kc: Any
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_used: float = field(default_factory=time.time)


class KernelPool:
    """Manages persistent IPython kernels keyed by session_id."""

    def __init__(self) -> None:
        self._kernels: dict[str, SessionKernel] = {}
        self._pool_lock = asyncio.Lock()

    async def _start_kernel(self, session_id: str) -> SessionKernel:
        ws = ensure_session_workspace(session_id)
        env = os.environ.copy()
        # Strip secrets from sandbox kernel environment
        for secret_key in ("OPENROUTER_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY", "GEMINI_API_KEY2", "TAVILY_API_KEY"):
            env.pop(secret_key, None)
        env["MPLBACKEND"] = "Agg"
        env["PYTHONUNBUFFERED"] = "1"

        km = AsyncKernelManager(kernel_name="python3")
        await km.start_kernel(cwd=str(ws), env=env)
        kc = km.client()
        kc.start_channels()
        await kc.wait_for_ready(timeout=20)

        sk = SessionKernel(session_id=session_id, workspace=ws, km=km, kc=kc)
        await self._run_raw(sk, BOOTSTRAP_CODE, timeout=15)
        return sk

    async def get_kernel(self, session_id: str) -> SessionKernel:
        async with self._pool_lock:
            existing = self._kernels.get(session_id)
            if existing is not None and await existing.km.is_alive():
                existing.last_used = time.time()
                return existing
            if existing is not None:
                await self._shutdown_entry(existing)
            created = await self._start_kernel(session_id)
            self._kernels[session_id] = created
            return created

    def _snapshot_files(self, ws: Path) -> dict[str, float]:
        snap: dict[str, float] = {}
        for folder in (ws / "outputs", ws):
            if not folder.exists():
                continue
            for p in folder.iterdir():
                if p.is_file() and not p.name.startswith("."):
                    rel = p.relative_to(ws).as_posix()
                    snap[rel] = p.stat().st_mtime_ns
        return snap

    async def _run_raw(
        self, sk: SessionKernel, code: str, timeout: float
    ) -> dict[str, Any]:
        msg_id = sk.kc.execute(code, stop_on_error=True)
        stdout_parts: list[str] = []
        stderr_parts: list[str] = []
        result_parts: list[str] = []
        error_trace: str | None = None

        deadline = time.perf_counter() + timeout

        while True:
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                await sk.km.interrupt_kernel()
                return {
                    "status": "timeout",
                    "error": f"Eksekusi kode melebihi batas waktu {timeout} detik dan dihentikan.",
                    "stdout": "".join(stdout_parts),
                }
            try:
                msg = await asyncio.wait_for(
                    sk.kc.get_iopub_msg(), timeout=min(remaining, 1.0)
                )
            except asyncio.TimeoutError:
                continue

            if msg.get("parent_header", {}).get("msg_id") != msg_id:
                continue

            msg_type = msg.get("msg_type")
            content = msg.get("content", {})

            if msg_type == "stream":
                text = content.get("text", "")
                if content.get("name") == "stderr":
                    stderr_parts.append(text)
                else:
                    stdout_parts.append(text)
            elif msg_type in ("execute_result", "display_data"):
                data = content.get("data", {})
                if "text/plain" in data:
                    result_parts.append(str(data["text/plain"]))
            elif msg_type == "error":
                tb = content.get("traceback", [])
                cleaned_tb = "\n".join(strip_ansi(line) for line in tb)
                if not cleaned_tb:
                    cleaned_tb = f"{content.get('ename', 'Error')}: {content.get('evalue', '')}"
                error_trace = cleaned_tb
            elif msg_type == "status" and content.get("execution_state") == "idle":
                break

        stdout_str = strip_ansi("".join(stdout_parts))
        stderr_str = strip_ansi("".join(stderr_parts))
        result_str = strip_ansi("\n".join(result_parts))

        if len(stdout_str) > PY_OUTPUT_LIMIT:
            stdout_str = stdout_str[:PY_OUTPUT_LIMIT] + "\n...[stdout dipotong]..."
        if len(result_str) > PY_OUTPUT_LIMIT:
            result_str = result_str[:PY_OUTPUT_LIMIT] + "\n...[result dipotong]..."
        if error_trace and len(error_trace) > PY_OUTPUT_LIMIT:
            error_trace = error_trace[-PY_OUTPUT_LIMIT:]

        payload: dict[str, Any] = {
            "status": "error" if error_trace else "ok",
        }
        if stdout_str.strip():
            payload["stdout"] = stdout_str.strip()
        if result_str.strip():
            payload["result"] = result_str.strip()
        if stderr_str.strip():
            payload["stderr"] = stderr_str.strip()
        if error_trace:
            payload["error"] = error_trace
            payload["hint"] = (
                "Periksa pesan traceback di atas, perbaiki kode Python, lalu panggil execute_python kembali."
            )
        return payload

    async def execute(
        self, session_id: str, code: str, timeout: float = PY_EXEC_TIMEOUT
    ) -> dict[str, Any]:
        sk = await self.get_kernel(session_id)
        async with sk.lock:
            sk.last_used = time.time()
            before = self._snapshot_files(sk.workspace)
            started = time.perf_counter()
            res = await self._run_raw(sk, code, timeout=timeout)
            after = self._snapshot_files(sk.workspace)
            res["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 1)

            new_or_updated = [
                rel
                for rel, mtime in sorted(after.items())
                if rel not in before or mtime > before[rel]
            ]
            if new_or_updated:
                res["new_files"] = new_or_updated
            return res

    async def _shutdown_entry(self, sk: SessionKernel) -> None:
        try:
            sk.kc.stop_channels()
        except Exception:
            pass
        try:
            await sk.km.shutdown_kernel(now=True)
        except Exception:
            pass

    async def shutdown_session(self, session_id: str) -> None:
        async with self._pool_lock:
            sk = self._kernels.pop(session_id, None)
            if sk is not None:
                await self._shutdown_entry(sk)

    async def reap_idle(self) -> None:
        now = time.time()
        async with self._pool_lock:
            expired = [
                sid
                for sid, sk in self._kernels.items()
                if (now - sk.last_used) > KERNEL_IDLE_TTL
            ]
            for sid in expired:
                sk = self._kernels.pop(sid, None)
                if sk is not None:
                    await self._shutdown_entry(sk)

    async def shutdown_all(self) -> None:
        async with self._pool_lock:
            entries = list(self._kernels.values())
            self._kernels.clear()
        for sk in entries:
            await self._shutdown_entry(sk)


kernel_pool = KernelPool()
