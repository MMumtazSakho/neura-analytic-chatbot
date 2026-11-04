FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MPLCONFIGDIR=/tmp/matplotlib \
    IPYTHONDIR=/tmp/ipython \
    JUPYTER_RUNTIME_DIR=/tmp/jupyter_runtime \
    PORT=8080

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    python -m ipykernel install --sys-prefix --name python3 --display-name "Python 3"

COPY app ./app
COPY static ./static
COPY scripts ./scripts
COPY sample_files ./sample_files
COPY data ./data

# Sanitasi data di dalam image container:
# 1. Hapus chunks ChromaDB & baris catalog untuk dokumen non-seed (id > 4)
# 2. Reset enabled=1 untuk semua dokumen seed bawaan
# 3. Kosongkan riwayat sesi chat testing lokal
RUN python -c "\
import sqlite3, pathlib; \
from app.kb.store import delete_document_chunks; \
db = pathlib.Path('/app/data/app_state.db'); \
conn = sqlite3.connect(str(db)); \
non_seed = [r[0] for r in conn.execute('SELECT id FROM kb_catalog WHERE id > 4').fetchall()]; \
[delete_document_chunks(doc_id) for doc_id in non_seed]; \
conn.execute('DELETE FROM kb_catalog WHERE id > 4'); \
conn.execute('UPDATE kb_catalog SET enabled = 1'); \
tables = [r[0] for r in conn.execute(\"SELECT name FROM sqlite_master WHERE type='table'\").fetchall()]; \
[conn.execute(f'DELETE FROM \"{t}\"') for t in ('session_meta', 'events', 'sessions', 'user_states', 'app_states') if t in tables]; \
conn.commit(); conn.execute('VACUUM'); conn.close(); \
[p.unlink() for p in pathlib.Path('/app/data/kb_images').glob('doc[56789]*')]"

EXPOSE 8080

CMD ["sh", "-c", "uvicorn app.server:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1"]
