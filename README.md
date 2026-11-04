# AI-Powered Data Analytics Chatbot

See the [Architecture Overview](docs/README.md) for concise English documentation and system, agent, and indexing diagrams.

Aplikasi chatbot analitik data dan investigasi operasional berbasis **Google Agent Development Kit (`google-adk`)** dengan pendekatan **Single Multi-Step `LlmAgent`** (`z-ai/glm-5.3-flash` via OpenRouter) serta **Multimodal RAG** (`google/gemini-embedding-2` + ChromaDB).

## Arsitektur & Tools

Single `LlmAgent` dilengkapi dengan 7 tool spesialis yang dapat dipanggil secara berurutan (*multi-step reasoning*):

| Tool | Fungsi |
|---|---|
| `get_database_schema` | Membaca struktur tabel SQLite (`transactions`, `merchants`), deskripsi bisnis kolom, rentang tanggal, dan daftar nilai unik (`distinct_values`). |
| `run_sql_query` | Menjalankan query `SELECT` / `WITH` secara *read-only* (`mode=ro` + SQLite authorizer) dengan mekanisme *self-correction*. |
| `execute_python` | Menjalankan kode Python di dalam **kernel Jupyter persisten per sesi** (`pandas`, `numpy`, `matplotlib`, `seaborn`, `pymupdf`, `python-docx`). |
| `view_image` | Melihat gambar secara *native* (grafik di `outputs/` maupun diagram hasil ekstraksi PDF di `kb_images/`) melalui `before_model_callback`. |
| `get_knowledge_list` | Menampilkan katalog dokumen yang ter-index di Knowledge Base beserta `id`, deskripsi, dan dataset di `shared/`. |
| `search_knowledge_base` | Pencarian semantik multimodal (teks & gambar PDF) di ChromaDB menggunakan `google/gemini-embedding-2`, mendukung filter `data=[id]`. |
| `get_chunk` | Mengambil chunk teks berdasarkan `doc_id` dan `chunk_id` asli, termasuk chunk tetangga hasil retrieval/reranking. |

### Reranker teks opsional

Tambahkan ke `neura/.env` (workspace) atau `analytics_chatbot/.env`:

```dotenv
RERANK_ENABLED=true
RERANK_MODEL=qwen/qwen3-reranker-8b
RERANK_TIMEOUT_SECONDS=8
RERANK_TEXT_CANDIDATES=20
RERANK_IMAGE_TOP_K=2
RERANK_FINAL_TOP_K=6
```

Contoh lengkap tanpa secret tersedia di `.env.example`. API key dan base URL memakai
`OPENROUTER_API_KEY` dan `OPENROUTER_BASE` yang sama dengan embedding. Konfigurasi
dibaca saat startup: restart server setelah mengubah `.env`. Environment proses
mendahului `.env` workspace, lalu `.env` proyek hanya mengisi variabel yang belum ada.

- Default kode `RERANK_ENABLED=false`. Nilai boolean: `true/false`, `1/0`, `yes/no`
  (kapitalisasi bebas); nilai lain ditolak.
- Nonaktif: top-6 gabungan teks/gambar seperti sebelumnya, tanpa request reranker.
- Aktif: ambil maksimal 20 teks dan 2 gambar secara terpisah. Teks dinilai melalui
  `POST /api/v1/rerank`; gambar melewati reranker. Pilih teks untuk sisa kuota enam
  hasil, lalu tambahkan gambar. Jika gambar kosong, kuota dapat diisi enam teks.
- Kandidat tunggal dilewati. Timeout keseluruhan delapan detik, HTTP error, atau
  respons tidak valid memakai urutan similarity sebagai fallback, tanpa retry.
- Hasil teks mempertahankan `doc_id`, `chunk_id`, `filename`, `page`, dan `text`.
  `score` tetap similarity embedding; `rerank_score` adalah skor relevansi terpisah.
  Gunakan `get_chunk(doc_id=6, chunk_ids=[17, 19])` untuk tetangga chunk 18 dokumen 6.
- Respons tool menambahkan `rerank_status` (`disabled/applied/skipped/fallback`)
  dan `rerank_elapsed_ms`. Log hanya mencatat status, jumlah kandidat, durasi,
  dan kategori error; tidak mencatat query, isi dokumen, atau API key.
- Tidak perlu indexing ulang atau migrasi ChromaDB. Script deployment meneruskan
  konfigurasi tervalidasi lewat file environment temporer privat, lalu menghapusnya.

Kontrak API: https://openrouter.ai/docs/api/api-reference/rerank/create-rerank

Pengujian lokal (tanpa API berbayar):

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Untuk menguji client melalui koneksi HTTP lokal, jalankan simulasi di terminal pertama:

```bash
postman simulation run postman/simulations/rerank/rerank.sim.yaml --port 4017 --no-history --output ndjson
```

Lalu di terminal kedua:

```bash
RERANK_SIMULATION_BASE=http://127.0.0.1:4017/openrouter/api/v1 .venv/bin/python -m unittest discover -s tests -v
```

Simulasi menguji sukses, HTTP 429/503, dan timeout tanpa mengakses provider asli.
Mock memakai urutan fixture deterministik, bukan penilaian semantik. Hentikan dengan
Ctrl+C. Hasil perbandingan retrieval API asli tersimpan di `tests/rerank_validation.json`;
pengukuran tersebut belum membuktikan peningkatan kualitas jawaban akhir.

### Aturan Routing File Upload
- **CSV / Excel (`.csv`, `.xlsx`) via Chat**: Masuk ke `uploads/` pada workspace Python sesi terkait.
- **CSV / Excel via Knowledge Base**: Melewati proses vector indexing dan langsung tersedia di folder `shared/` pada seluruh sesi Python workspace.
- **PDF ($\le 5$ halaman) & TXT/DOCX ($\le 12.000$ karakter) via Chat**: Masuk ke `uploads/` untuk dibaca langsung oleh Python tool.
- **PDF ($> 5$ halaman), TXT/DOCX ($> 12.000$ karakter) via Chat, atau Dokumen via Knowledge Base**: Di-ekstrak (teks + gambar $\ge 120\times 120$ px) dan di-index ke ChromaDB.

---

## Cara Menjalankan

1. Pastikan file `.env` di root workspace (`neura/.env`) memiliki `OPENROUTER_API_KEY`:
   ```env
   OPENROUTER_API_KEY=sk-or-v1-...
   ```

2. Masuk ke direktori `analytics_chatbot` dan aktifkan virtual environment:
   ```bash
   cd analytics_chatbot
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

3. Generate database transaksi sintetis (`transactions.db`), dokumen SOP/Insiden/Kebijakan, dan file spreadsheet sampel:
   ```bash
   python scripts/generate_data.py
   ```

4. Index dokumen awal ke Knowledge Base (ChromaDB):
   ```bash
   python scripts/seed_kb.py
   ```

5. Jalankan server FastAPI:
   ```bash
   uvicorn app.server:app --reload --port 8000
   ```

6. Buka `http://127.0.0.1:8000` di browser.

---

## Contoh Pertanyaan Demo

- **Eksplorasi Data**: *"Berapa total transaksi pada bulan ini, bagaimana distribusinya berdasarkan status, dan kanal mana yang memiliki volume tertinggi?"*
- **Tren & Perbandingan**: *"Bagaimana perbandingan volume transaksi dan tingkat keberhasilan dalam tiga bulan terakhir? Kategori mana yang mengalami peningkatan atau penurunan terbesar?"*
- **Investigasi Masalah**: *"Mengapa tingkat kegagalan transaksi meningkat dibandingkan bulan sebelumnya? Kanal, bank, kode error apa yang paling berkontribusi, dan sejak tanggal berapa perubahan tersebut mulai terjadi?"*
- **Pertanyaan Dokumen**: *"Berdasarkan dokumen yang tersedia, apa penyebab umum kegagalan transaksi dan apa perbedaan prosedur penanganan untuk setiap jenis kode error? Tampilkan juga diagram alurnya."*
- **Follow-up & Visualisasi**: *"Apakah informasi dalam dokumen laporan insiden relevan untuk menjelaskan pola kenaikan kegagalan di bulan Oktober? Buatkan juga grafik tren hariannya."*
