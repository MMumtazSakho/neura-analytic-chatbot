# Analytics Chatbot Architecture

[Live Demo](https://neura-analytics-chatbot-1098550132960.asia-southeast2.run.app)

Analytics Chatbot combines operational SQL analysis, spreadsheet processing, and multimodal document retrieval through a single Google ADK agent. FastAPI serves the web interface and streams answers and tool events to the browser.

## System Overview

Users can ask questions or upload knowledge data. The application runs on Google Cloud Run; model inference, embeddings, and optional reranking use OpenRouter APIs.

![System architecture](diagram/system-architecture-v2.png)

SQLite stores operational data, conversation state, and the knowledge catalog. ChromaDB stores document embeddings. Uploaded files, extracted images, and generated outputs remain on the application filesystem. The current Cloud Run configuration does not provide persistent external storage.

## Agent Execution

The **Analyst** agent uses `glm-5.3-flash` with system instructions and conversation history. It selects tools as needed, evaluates their results, and repeats until it can produce an answer. Tools are independent capabilities, not a fixed execution sequence.

![Agent tool execution flow](diagram/agent-tool-execution-flow-v2.png)

| Capability | Tools |
|---|---|
| Read-only SQL analysis | `get_database_schema`, `run_sql_query` |
| Persistent Python analysis | `execute_python` |
| Knowledge retrieval | `get_knowledge_list`, `search_knowledge_base`, `get_chunk` |
| Visual inspection | `view_image` |

Image inspection injects image content into the next model request. Answers can include tables, document citations, and generated charts.

## Data Access and Analysis

The agent combines three paths: RAG retrieves document context, NL2SQL queries operational data, and Python analyzes workspace files. Each path returns results to the agent and can be repeated before the final answer.

![Data access and analysis](diagram/data-access-and-analysis.png)

RAG search respects document visibility, readiness, and enabled status. With `RERANK_ENABLED=true`, text candidates are reranked by `qwen/qwen3-reranker-8b`; image candidates retain embedding-based ranking. API failures or timeouts fall back to vector ordering.

![Bi-Encoder vs Cross-Encoder retrieval](diagram/bi-encoder-vs-cross-encoder.png)

- **Bi-Encoder**: Independently encodes queries and document chunks into dense vectors for fast cosine similarity search in ChromaDB.
- **Cross-Encoder**: Jointly encodes query and candidate text pairs with full cross-attention for higher scoring precision during reranking.

Results preserve the original `doc_id` and `chunk_id`, allowing `get_chunk` to retrieve neighboring text. Disabling reranking restores the original combined vector search. Environment changes require a server restart.

## Knowledge Upload and Indexing

Document ingestion extracts text and embedded images, chunks the text, and embeds both content types using `google/gemini-embedding-2`. Text and image entries share one ChromaDB collection while retaining separate identities and source metadata.

![Knowledge upload and document indexing](diagram/knowledge-upload-and-indexing-v2.png)

Excel and CSV files bypass vector indexing. Chat attachments are stored in the session workspace; knowledge-base datasets are shared across sessions and registered in the catalog. The agent analyzes them through Python when needed. Short documents uploaded in chat also bypass indexing; knowledge-base documents and larger chat documents follow the indexing pipeline.

See the [project README](../README.md) for setup, configuration, and deployment instructions.
