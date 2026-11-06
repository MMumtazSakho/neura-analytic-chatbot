"""SSE normalization for ADK streaming events and session history reconstruction."""

from __future__ import annotations

import json
import time
import uuid
from typing import Any


DISPLAYED_TOOLS = {
    "get_database_schema",
    "run_sql_query",
    "execute_python",
    "view_image",
    "get_knowledge_list",
    "search_knowledge_base",
    "get_chunk",
}


def sse(data: dict[str, Any]) -> str:
    """Format a dictionary as a Server-Sent Event frame."""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def json_safe(value: Any) -> Any:
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except (TypeError, ValueError):
        return str(value)


def text_from_event(event: Any) -> str:
    content = getattr(event, "content", None)
    parts = getattr(content, "parts", None) if content else None
    if not parts:
        return ""
    texts: list[str] = []
    for part in parts:
        t = getattr(part, "text", None)
        if t and not getattr(part, "thought", False):
            texts.append(t)
    return "".join(texts)


def event_is_final(event: Any) -> bool:
    final_marker = getattr(event, "is_final_response", False)
    return bool(final_marker() if callable(final_marker) else final_marker)


def event_function_calls(event: Any) -> list[Any]:
    getter = getattr(event, "get_function_calls", None)
    if callable(getter):
        return getter() or []
    return []


def event_function_responses(event: Any) -> list[Any]:
    getter = getattr(event, "get_function_responses", None)
    if callable(getter):
        return getter() or []
    return []


class EventNormalizer:
    """Convert ADK runner events into clean SSE frames for the UI."""

    def __init__(self) -> None:
        self._active: dict[str, dict[str, Any]] = {}
        self._tool_calls: dict[str, dict[str, Any]] = {}
        self._latest_tool_by_name: dict[tuple[str, str], str] = {}

    def feed(self, event: Any) -> list[dict[str, Any]]:
        author = getattr(event, "author", None) or "Analyst"
        text = text_from_event(event)
        partial = bool(getattr(event, "partial", False))
        active = self._active.get(author)
        output: list[dict[str, Any]] = []

        if text:
            if active is None:
                active = {
                    "message_id": uuid.uuid4().hex,
                    "partial_seen": False,
                }
                self._active[author] = active

            if partial:
                active["partial_seen"] = True
                output.append(
                    {
                        "type": "message_delta",
                        "message_id": active["message_id"],
                        "agent": author,
                        "text": text,
                    }
                )
            elif not active["partial_seen"]:
                output.append(
                    {
                        "type": "message_delta",
                        "message_id": active["message_id"],
                        "agent": author,
                        "text": text,
                    }
                )

        if not partial:
            for fn_call in event_function_calls(event):
                tool_name = getattr(fn_call, "name", None) or "tool"
                if tool_name not in DISPLAYED_TOOLS:
                    continue
                # If an agent text bubble was open before a tool call, close it cleanly
                if active is not None:
                    output.append(
                        {
                            "type": "message_end",
                            "message_id": active["message_id"],
                            "agent": author,
                        }
                    )
                    self._active.pop(author, None)
                    active = None

                tool_id = getattr(fn_call, "id", None) or uuid.uuid4().hex
                tool_key = (author, tool_name)
                self._tool_calls[tool_id] = {
                    "agent": author,
                    "tool": tool_name,
                    "started_at": time.perf_counter(),
                }
                self._latest_tool_by_name[tool_key] = tool_id
                output.append(
                    {
                        "type": "tool_start",
                        "tool_id": tool_id,
                        "agent": author,
                        "tool": tool_name,
                        "args": json_safe(getattr(fn_call, "args", {}) or {}),
                    }
                )

        for fn_resp in event_function_responses(event):
            tool_name = getattr(fn_resp, "name", None) or "tool"
            tool_id = getattr(fn_resp, "id", None)
            if tool_id not in self._tool_calls and tool_name not in DISPLAYED_TOOLS:
                continue
            if not tool_id:
                tool_id = self._latest_tool_by_name.get((author, tool_name))
            if not tool_id:
                tool_id = uuid.uuid4().hex
            tool_info = self._tool_calls.get(
                tool_id,
                {"agent": author, "tool": tool_name, "started_at": time.perf_counter()},
            )
            duration_ms = round((time.perf_counter() - tool_info["started_at"]) * 1000, 1)
            output.append(
                {
                    "type": "tool_result",
                    "tool_id": tool_id,
                    "agent": tool_info["agent"],
                    "tool": tool_info["tool"],
                    "duration_ms": duration_ms,
                    "output": json_safe(getattr(fn_resp, "response", {}) or {}),
                }
            )

        if event_is_final(event) and active is not None:
            output.append(
                {
                    "type": "message_end",
                    "message_id": active["message_id"],
                    "agent": author,
                }
            )
            self._active.pop(author, None)

        return output

    def finish(self) -> list[dict[str, Any]]:
        output = [
            {
                "type": "message_end",
                "message_id": state["message_id"],
                "agent": author,
            }
            for author, state in self._active.items()
        ]
        self._active.clear()
        return output


def strip_system_attachment_notes(user_text: str) -> tuple[str, list[str]]:
    """Separate "[Lampiran Chat: ...]" metadata notes from user's display message."""
    attachments: list[str] = []
    lines: list[str] = []
    for line in (user_text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("[Lampiran Chat:") and stripped.endswith("]"):
            attachments.append(stripped)
        else:
            lines.append(line)
    return "\n".join(lines).strip(), attachments


def reconstruct_session_timeline(session: Any) -> list[dict[str, Any]]:
    """Rebuild an ordered timeline of user messages, tool steps, and assistant replies from ADK session events."""
    events = getattr(session, "events", None) or []
    timeline: list[dict[str, Any]] = []
    tool_map: dict[str, dict[str, Any]] = {}

    for ev in events:
        if getattr(ev, "partial", False):
            continue
        author = getattr(ev, "author", None) or ""
        content = getattr(ev, "content", None)
        role = getattr(content, "role", "") if content else ""

        if author == "user" or role == "user":
            raw_text = text_from_event(ev)
            if raw_text.startswith("[Visual Inspection Result dari view_image"):
                continue
            clean_text, notes = strip_system_attachment_notes(raw_text)
            if clean_text or notes:
                timeline.append(
                    {
                        "kind": "user",
                        "text": clean_text,
                        "attachments": notes,
                    }
                )
            continue

        text = text_from_event(ev)
        if text:
            timeline.append(
                {
                    "kind": "assistant",
                    "agent": author or "Analyst",
                    "text": text,
                }
            )

        for fn_call in event_function_calls(ev):
            tname = getattr(fn_call, "name", None) or "tool"
            if tname not in DISPLAYED_TOOLS:
                continue
            tid = getattr(fn_call, "id", None) or uuid.uuid4().hex
            entry = {
                "kind": "tool",
                "tool_id": tid,
                "agent": author or "Analyst",
                "tool": tname,
                "args": json_safe(getattr(fn_call, "args", {}) or {}),
                "output": None,
            }
            tool_map[tid] = entry
            timeline.append(entry)

        for fn_resp in event_function_responses(ev):
            tname = getattr(fn_resp, "name", None) or "tool"
            tid = getattr(fn_resp, "id", None)
            if tid and tid in tool_map:
                tool_map[tid]["output"] = json_safe(getattr(fn_resp, "response", {}) or {})

    return timeline
