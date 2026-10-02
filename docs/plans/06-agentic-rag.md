# Plan 6: ideas from production-agentic-rag-course

Source: [jamwithai/production-agentic-rag-course](https://github.com/jamwithai/production-agentic-rag-course), an arXiv assistant built on FastAPI, OpenSearch, Ollama, LangGraph, Langfuse, Redis, Airflow and Telegram. Its agent graph is guardrail → retrieve → grade documents → (rewrite query → retrieve) → generate, with Langfuse spans on each node.

## What we took
| Course | Here | Why it fits |
|---|---|---|
| Grade documents node (LLM yes/no relevance) | `LibraryChat._grade`: in live mode a fast model lists which retrieved passages help; the rest are dropped before generation. Off with `AGENTKIT_GRADE=0` | Less noise for the answer model; the extractive relevance gate stays as the first filter |
| Rewrite query node, then retrieve again | `LibraryChat._rewrite` + up to `AGENTKIT_MAX_RETRIEVAL_ATTEMPTS` (default 2). Live: fast-model rewrite into key terms (EN + AR). Offline: spelling fixed against the index vocabulary, Franco-Arabic transliterated | A dead end becomes an answer when the first query was badly spelled or phrased |
| Langfuse spans per node | `Trace`: route, catalog, retrieve (attempt, results), grade (kept, dropped), rewrite (changed), generate (cited, degraded) with milliseconds, in every answer (`trace`) and `--debug` | Same visibility without another service; holds counts only, never question text |
| `/hybrid-search` endpoint | `GET /api/search?q=&k=`: ranked passages the caller may see, no model call | Staff debugging, and a fallback that works during an outage |
| Prometheus-style counters | `agentkit_rewrites_total`, `agentkit_grade_dropped_total` | Shows how often the loop fires |

## What we left out, and why
| Course | Reason |
|---|---|
| LLM scope guardrail (0–100 score) before retrieval | Costs a model call on every question; regex guards plus the retrieval gate already send out-of-scope questions to a librarian (dev h22, h24 stay handoffs after the retry) |
| LangGraph, Langfuse, OpenSearch, Redis, Airflow | Extra services for a single-library deployment; the same behaviour is a few functions here. Qdrant stays optional for large corpora |
| Telegram bot | Egyptian students use WhatsApp, which is already a channel |
| Gradio UI | We have an accessible bilingual UI (WCAG 2.2 AA) |
| Word-window chunking (600 words, 100 overlap) | Our chunks follow sections and sentences, which keeps citations precise |
