"""ADK tool wrapper around the persistent per-session Python kernel."""

from __future__ import annotations

from typing import Any

from google.adk.tools.tool_context import ToolContext

from ..sandbox.kernel_pool import kernel_pool


async def execute_python(code: str, tool_context: ToolContext) -> dict[str, Any]:
    """Execute Python code in a persistent per-session Jupyter workspace (pandas, numpy, matplotlib, seaborn, fitz/PyMuPDF, docx).

    Variables, DataFrames, and imports persist across calls within the same chat session.
    Working directory structure:
      - 'sql_results/' : CSV files saved by `run_sql_query(..., save=True)` or auto-saved large SQL results (e.g. `pd.read_csv('sql_results/query_1.csv')`)
      - 'uploads/'     : files uploaded in the current chat session (CSV, XLSX, small PDF/TXT/DOCX)
      - 'shared/'      : shared CSV/XLSX datasets uploaded via the Knowledge Base
      - 'outputs/'     : save any generated matplotlib/seaborn charts here (e.g. plt.savefig('outputs/chart.png', bbox_inches='tight'))

    Always print() summary tables or metrics you want to inspect. After saving a chart to 'outputs/<name>.png',
    call view_image('outputs/<name>.png') to inspect it visually before embedding it in your final response.

    Args:
        code: Python code to execute in the session kernel.
    """
    clean_code = (code or "").strip()
    if not clean_code:
        return {"error": "Kode Python tidak boleh kosong."}

    session = getattr(tool_context, "session", None)
    session_id = getattr(session, "id", None) or "default"
    return await kernel_pool.execute(session_id=session_id, code=clean_code)
