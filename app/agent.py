"""Single LlmAgent setup with Google ADK and OpenRouter LiteLlm."""

from __future__ import annotations

from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm

from .callbacks import inject_pending_images
from .config import LLM_MODEL, OPENROUTER_BASE, get_openrouter_api_key
from .prompts import SYSTEM_INSTRUCTION
from .tools.image_tool import view_image
from .tools.kb_tools import get_chunk, get_knowledge_list, search_knowledge_base
from .tools.python_tool import execute_python
from .tools.sql_tools import get_database_schema, run_sql_query


def build_agent() -> LlmAgent:
    """Create the single multi-step analytics LlmAgent backed by OpenRouter."""
    api_key = get_openrouter_api_key()
    model = LiteLlm(
        model=LLM_MODEL,
        api_key=api_key,
        api_base=OPENROUTER_BASE,
    )
    return LlmAgent(
        name="Analyst",
        model=model,
        description="AI-powered data analytics & operational investigation assistant.",
        instruction=SYSTEM_INSTRUCTION,
        tools=[
            get_database_schema,
            run_sql_query,
            execute_python,
            view_image,
            get_knowledge_list,
            search_knowledge_base,
            get_chunk,
        ],
        before_model_callback=inject_pending_images,
    )
