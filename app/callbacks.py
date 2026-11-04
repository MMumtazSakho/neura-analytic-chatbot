"""ADK model callbacks for injecting native multimodal images from view_image."""

from __future__ import annotations

from typing import Any

from google.adk.agents.callback_context import CallbackContext
from google.adk.models import LlmRequest, LlmResponse
from google.genai import types

# Queue of pending images per session_id: list of (display_path, png_bytes, mime_type)
PENDING_IMAGES: dict[str, list[tuple[str, bytes, str]]] = {}


def queue_image_for_session(
    session_id: str, display_path: str, image_bytes: bytes, mime_type: str = "image/png"
) -> None:
    """Stage an image to be injected natively into the next LLM call for this session."""
    bucket = PENDING_IMAGES.setdefault(session_id, [])
    bucket.append((display_path, image_bytes, mime_type))


def pop_queued_images(session_id: str) -> list[tuple[str, bytes, str]]:
    """Retrieve and clear all staged images for a session."""
    return PENDING_IMAGES.pop(session_id, [])


async def inject_pending_images(
    callback_context: CallbackContext,
    llm_request: LlmRequest,
) -> LlmResponse | None:
    """Inject queued image parts from view_image into llm_request.contents before model call."""
    session = getattr(callback_context, "session", None)
    session_id = getattr(session, "id", None) or "default"
    images = pop_queued_images(session_id)
    if not images:
        return None

    parts: list[types.Part] = [
        types.Part.from_text(
            text="[Visual Inspection Result dari view_image — berikut adalah gambar yang kamu minta untuk dilihat secara native:]"
        )
    ]
    for display_path, data, mime_type in images:
        parts.append(types.Part.from_text(text=f"File gambar: {display_path}"))
        parts.append(types.Part.from_bytes(data=data, mime_type=mime_type))

    if llm_request.contents is None:
        llm_request.contents = []

    llm_request.contents.append(types.Content(role="user", parts=parts))
    return None
