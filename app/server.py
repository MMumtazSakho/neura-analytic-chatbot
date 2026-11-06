"""FastAPI server for the AI-Powered Data Analytics Chatbot."""

from __future__ import annotations

import asyncio
import logging
import shutil
import sqlite3
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.runners import Runner
from google.adk.sessions import DatabaseSessionService
from google.genai import types
from pydantic import BaseModel, Field

from .agent import build_agent
from .config import (
    APP_NAME,
    APP_STATE_DB_PATH,
    DATA_DIR,
    DEFAULT_USER_ID,
    KB_FILES_DIR,
    KB_IMAGES_DIR,
    MAX_LLM_CALLS,
    STATIC_DIR,
    WORKSPACES_DIR,
    ensure_runtime_dirs,
)
from .kb.catalog import (
    get_catalog_entry,
    list_catalog_entries,
    set_all_catalog_enabled,
    set_catalog_entry_enabled,
)
from .kb.ingest import remove_document_completely
from .sandbox.kernel_pool import ensure_session_workspace, kernel_pool
from .streaming import EventNormalizer, reconstruct_session_timeline, sse
from .tools.sql_tools import list_databases_info, preview_database_table
from .uploads import handle_chat_upload, handle_kb_upload

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ChatRequest(BaseModel):
    session_id: str | None = Field(default=None, max_length=128)
    message: str = Field(min_length=1, max_length=20_000)
    attachment_notes: list[str] = Field(default_factory=list)


class KbToggleRequest(BaseModel):
    enabled: bool | None = None
    session_id: str | None = None


@dataclass
class ActiveTurnRun:
    session_id: str
    user_text: str
    attachment_notes: list[str]
    baseline_timeline: list[dict[str, Any]]
    frames: list[dict[str, Any]] = field(default_factory=list)
    cond: asyncio.Condition = field(default_factory=asyncio.Condition)
    done: bool = False
    task: asyncio.Task[None] | None = None

    async def publish(self, event_dict: dict[str, Any]) -> None:
        async with self.cond:
            self.frames.append(event_dict)
            self.cond.notify_all()

    async def mark_done(self) -> None:
        async with self.cond:
            self.frames.append({"type": "done"})
            self.done = True
            self.cond.notify_all()


_ACTIVE_RUNS: dict[str, ActiveTurnRun] = {}


async def _execute_turn_in_background(
    runner: Runner,
    run_state: ActiveTurnRun,
) -> None:
    normalizer = EventNormalizer()
    try:
        await run_state.publish({"type": "session", "session_id": run_state.session_id})

        full_prompt = run_state.user_text
        if run_state.attachment_notes:
            notes_block = "\n".join(run_state.attachment_notes)
            full_prompt = f"{notes_block}\n\n{run_state.user_text}"

        user_message = types.Content(
            role="user",
            parts=[types.Part.from_text(text=full_prompt)],
        )
        run_config = RunConfig(
            streaming_mode=StreamingMode.SSE,
            max_llm_calls=MAX_LLM_CALLS,
        )

        async for event in runner.run_async(
            user_id=DEFAULT_USER_ID,
            session_id=run_state.session_id,
            new_message=user_message,
            run_config=run_config,
        ):
            for normalized in normalizer.feed(event):
                await run_state.publish(normalized)

        for normalized in normalizer.finish():
            await run_state.publish(normalized)
    except asyncio.CancelledError:
        logger.info("Turn run untuk sesi %s dibatalkan.", run_state.session_id)
        raise
    except Exception as exc:
        logger.exception("Eksekusi chat ADK gagal")
        await run_state.publish(
            {
                "type": "error",
                "message": f"Gagal memproses permintaan: {exc}",
            }
        )
    finally:
        await run_state.mark_done()


async def _stream_turn_frames(run_state: ActiveTurnRun) -> AsyncIterator[str]:
    cursor = 0
    while True:
        async with run_state.cond:
            while cursor >= len(run_state.frames) and not run_state.done:
                await run_state.cond.wait()
            batch = run_state.frames[cursor:]
            cursor = len(run_state.frames)
            is_done = run_state.done

        for frame in batch:
            yield sse(frame)
            if frame.get("type") == "done":
                return

        if is_done and cursor >= len(run_state.frames):
            return


def _session_meta_conn() -> sqlite3.Connection:
    ensure_runtime_dirs()
    conn = sqlite3.connect(str(APP_STATE_DB_PATH), timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS session_meta (
            session_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def _upsert_session_meta(session_id: str, title: str | None = None) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with _session_meta_conn() as conn:
        existing = conn.execute(
            "SELECT title FROM session_meta WHERE session_id = ?", (session_id,)
        ).fetchone()
        if existing is None:
            conn.execute(
                "INSERT INTO session_meta (session_id, title, updated_at) VALUES (?, ?, ?)",
                (session_id, (title or "Percakapan baru")[:80], now),
            )
        else:
            new_title = existing["title"]
            if title and (new_title == "Percakapan baru" or not new_title.strip()):
                new_title = title[:80]
            conn.execute(
                "UPDATE session_meta SET title = ?, updated_at = ? WHERE session_id = ?",
                (new_title, now, session_id),
            )
        conn.commit()


def _delete_session_meta(session_id: str) -> None:
    with _session_meta_conn() as conn:
        conn.execute("DELETE FROM session_meta WHERE session_id = ?", (session_id,))
        conn.commit()


def _list_session_meta() -> list[dict[str, Any]]:
    with _session_meta_conn() as conn:
        rows = conn.execute(
            "SELECT session_id, title, updated_at FROM session_meta ORDER BY updated_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def build_runner() -> Runner:
    ensure_runtime_dirs()
    db_url = f"sqlite+aiosqlite:///{APP_STATE_DB_PATH.as_posix()}"
    session_service = DatabaseSessionService(db_url=db_url)
    agent = build_agent()
    return Runner(
        app_name=APP_NAME,
        agent=agent,
        session_service=session_service,
    )


async def _idle_kernel_reaper() -> None:
    while True:
        await asyncio.sleep(120)
        try:
            await kernel_pool.reap_idle()
        except Exception:
            pass


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    ensure_runtime_dirs()
    reaper_task = asyncio.create_task(_idle_kernel_reaper())
    try:
        app.state.runner = build_runner()
        app.state.configured = True
    except Exception as exc:
        logger.warning("Runner initialization deferred: %s", exc)
        app.state.runner = None
        app.state.configured = False
    try:
        yield
    finally:
        reaper_task.cancel()
        await kernel_pool.shutdown_all()


app = FastAPI(
    title="AI-Powered Data Analytics Chatbot",
    description="Single-agent Google ADK analytics & document investigation workspace.",
    lifespan=lifespan,
)
STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.middleware("http")
async def disable_static_cache(request: Request, call_next):
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return response


def _get_runner(request: Request) -> Runner:
    runner = getattr(request.app.state, "runner", None)
    if runner is None:
        try:
            runner = build_runner()
            request.app.state.runner = runner
            request.app.state.configured = True
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
    return runner


async def get_or_create_session(runner: Runner, session_id: str | None) -> Any:
    if session_id:
        existing = await runner.session_service.get_session(
            app_name=APP_NAME,
            user_id=DEFAULT_USER_ID,
            session_id=session_id,
        )
        if existing is not None:
            ensure_session_workspace(existing.id)
            return existing

    created = await runner.session_service.create_session(
        app_name=APP_NAME,
        user_id=DEFAULT_USER_ID,
        session_id=session_id,
    )
    ensure_session_workspace(created.id)
    _upsert_session_meta(created.id, "Percakapan baru")
    return created


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
async def health(request: Request) -> JSONResponse:
    configured = bool(getattr(request.app.state, "configured", False))
    return JSONResponse(
        {
            "status": "ok" if configured else "degraded",
            "configured": configured,
        }
    )


@app.get("/api/sessions")
async def list_sessions() -> JSONResponse:
    return JSONResponse({"sessions": _list_session_meta()})


@app.post("/api/sessions")
async def create_new_session(request: Request) -> JSONResponse:
    runner = _get_runner(request)
    session = await get_or_create_session(runner, None)
    return JSONResponse(
        {
            "session_id": session.id,
            "title": "Percakapan baru",
        }
    )


@app.get("/api/sessions/{session_id}")
async def get_session_detail(session_id: str, request: Request) -> JSONResponse:
    runner = _get_runner(request)
    session = await runner.session_service.get_session(
        app_name=APP_NAME,
        user_id=DEFAULT_USER_ID,
        session_id=session_id,
    )
    if session is None:
        raise HTTPException(status_code=404, detail="Sesi tidak ditemukan.")

    run_state = _ACTIVE_RUNS.get(session_id)
    if run_state is not None and not run_state.done:
        return JSONResponse(
            {
                "session_id": session.id,
                "timeline": run_state.baseline_timeline,
                "active_run": {
                    "user_text": run_state.user_text,
                    "attachments": run_state.attachment_notes,
                },
            }
        )

    timeline = reconstruct_session_timeline(session)
    return JSONResponse(
        {
            "session_id": session.id,
            "timeline": timeline,
            "active_run": None,
        }
    )


@app.get("/api/sessions/{session_id}/stream")
async def resume_session_stream(session_id: str) -> StreamingResponse:
    run_state = _ACTIVE_RUNS.get(session_id)
    if run_state is None:
        async def _empty_done() -> AsyncIterator[str]:
            yield sse({"type": "done"})

        return StreamingResponse(
            _empty_done(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    return StreamingResponse(
        _stream_turn_frames(run_state),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str, request: Request) -> JSONResponse:
    runner = _get_runner(request)
    active = _ACTIVE_RUNS.pop(session_id, None)
    if active and active.task and not active.task.done():
        active.task.cancel()
    await kernel_pool.shutdown_session(session_id)
    try:
        await runner.session_service.delete_session(
            app_name=APP_NAME,
            user_id=DEFAULT_USER_ID,
            session_id=session_id,
        )
    except Exception:
        pass
    _delete_session_meta(session_id)
    ws_dir = WORKSPACES_DIR / session_id
    if ws_dir.exists():
        shutil.rmtree(ws_dir, ignore_errors=True)
    return JSONResponse({"deleted": session_id})


@app.post("/api/sessions/{session_id}/attachments")
async def upload_chat_attachment(
    session_id: str,
    request: Request,
    file: UploadFile = File(...),
) -> JSONResponse:
    runner = _get_runner(request)
    session = await get_or_create_session(runner, session_id)
    raw_bytes = await file.read()
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="File kosong.")

    try:
        info = await handle_chat_upload(
            session_id=session.id,
            filename=file.filename or "attachment",
            file_bytes=raw_bytes,
        )
        return JSONResponse(info)
    except Exception as exc:
        logger.exception("Gagal memproses lampiran chat")
        raise HTTPException(status_code=500, detail=f"Gagal memproses file: {exc}") from exc


@app.get("/api/sessions/{session_id}/files/{file_path:path}")
async def serve_session_file(session_id: str, file_path: str) -> FileResponse:
    ws = (WORKSPACES_DIR / session_id).resolve()
    target = (ws / file_path).resolve()
    try:
        target.relative_to(ws)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Akses path ditolak.") from exc
    if not target.is_file():
        raise HTTPException(status_code=404, detail="File tidak ditemukan.")
    return FileResponse(target)


@app.get("/api/kb")
async def list_kb(session_id: str | None = None) -> JSONResponse:
    items = list_catalog_entries(session_id=session_id, include_all_sessions=False)
    return JSONResponse({"items": items})


@app.post("/api/kb")
async def upload_kb(
    file: UploadFile = File(...),
    description: str = Form(default=""),
) -> JSONResponse:
    raw_bytes = await file.read()
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="File kosong.")
    try:
        res = await handle_kb_upload(
            filename=file.filename or "document",
            file_bytes=raw_bytes,
            description=description.strip() or None,
        )
        return JSONResponse(res)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Gagal mengunggah file ke Knowledge Base")
        raise HTTPException(status_code=500, detail=f"Gagal memproses file KB: {exc}") from exc


@app.delete("/api/kb/{doc_id}")
async def delete_kb_item(doc_id: int) -> JSONResponse:
    ok = remove_document_completely(doc_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Item Knowledge Base tidak ditemukan.")
    return JSONResponse({"deleted": doc_id})


@app.patch("/api/kb/{doc_id}/toggle")
async def toggle_kb_item(doc_id: int, payload: KbToggleRequest | None = None) -> JSONResponse:
    enabled_arg = payload.enabled if payload is not None else None
    updated = set_catalog_entry_enabled(doc_id, enabled=enabled_arg)
    if updated is None:
        raise HTTPException(status_code=404, detail="Item Knowledge Base tidak ditemukan.")
    return JSONResponse(updated)


@app.post("/api/kb/toggle-all")
async def toggle_all_kb_items(payload: KbToggleRequest) -> JSONResponse:
    target = True if payload.enabled is None else bool(payload.enabled)
    items = set_all_catalog_enabled(enabled=target, session_id=payload.session_id)
    return JSONResponse({"items": items})


@app.get("/api/kb/{doc_id}/file")
async def serve_kb_file(doc_id: int) -> FileResponse:
    entry = get_catalog_entry(doc_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Item Knowledge Base tidak ditemukan.")

    filename = str(entry.get("filename") or f"doc_{doc_id}")
    candidates: list[Path] = []
    stored_path = entry.get("stored_path")
    if stored_path:
        candidates.append(Path(str(stored_path)))
    candidates.extend(
        [
            KB_FILES_DIR / f"doc{doc_id}_{filename}",
            KB_FILES_DIR / filename,
            DATA_DIR / "seed_docs" / filename,
        ]
    )

    target: Path | None = None
    for candidate in candidates:
        if candidate.is_file():
            target = candidate.resolve()
            break

    if target is None:
        raise HTTPException(status_code=404, detail="File dokumen tidak ditemukan di disk.")

    media_type = "application/pdf" if filename.lower().endswith(".pdf") else None
    return FileResponse(
        target,
        media_type=media_type,
        filename=filename,
        content_disposition_type="inline",
    )


@app.get("/api/kb/images/{image_name}")
async def serve_kb_image(image_name: str) -> FileResponse:
    root = KB_IMAGES_DIR.resolve()
    target = (root / image_name).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Akses ditolak.") from exc
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Gambar tidak ditemukan.")
    return FileResponse(target)


@app.get("/api/databases")
async def get_databases() -> JSONResponse:
    return JSONResponse({"databases": list_databases_info()})


@app.get("/api/databases/{db_id}/tables/{table_name}/preview")
async def get_table_preview(
    db_id: str,
    table_name: str,
    limit: int = 25,
    offset: int = 0,
) -> JSONResponse:
    try:
        data = preview_database_table(
            db_id=db_id,
            table_name=table_name,
            limit=limit,
            offset=offset,
        )
        return JSONResponse(data)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Gagal mengambil preview tabel database")
        raise HTTPException(status_code=500, detail=f"Gagal memuat preview tabel: {exc}") from exc


@app.post("/api/chat/stream")
async def chat_stream(payload: ChatRequest, request: Request) -> StreamingResponse:
    user_text = payload.message.strip()
    if not user_text:
        raise HTTPException(status_code=400, detail="Pesan tidak boleh kosong.")

    runner = _get_runner(request)
    session = await get_or_create_session(runner, payload.session_id)
    _upsert_session_meta(session.id, user_text)

    existing = _ACTIVE_RUNS.get(session.id)
    if existing is not None and not existing.done:
        # Join existing in-progress run if one is already running for this session
        run_state = existing
    else:
        baseline_timeline = reconstruct_session_timeline(session)
        run_state = ActiveTurnRun(
            session_id=session.id,
            user_text=user_text,
            attachment_notes=list(payload.attachment_notes or []),
            baseline_timeline=baseline_timeline,
        )
        _ACTIVE_RUNS[session.id] = run_state
        run_state.task = asyncio.create_task(
            _execute_turn_in_background(runner, run_state)
        )

    return StreamingResponse(
        _stream_turn_frames(run_state),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
