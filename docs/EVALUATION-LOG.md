# Evaluation log

Every review round and every evaluation run is recorded here with its exact numbers, including the bad ones. Offline runs use the deterministic extractive stand-in, not Claude, so answer-level scores are a floor: retrieval numbers carry over to live mode, sentence choice does not.

How to reproduce:
```bash
agentkit --index data/index.db ingest knowledge/auc-library/pages
agentkit --index data/index.db eval --set golden    # also: --set dev, --set heldout
agentkit redteam && agentkit test-agents && pytest -q && python scripts/ocr_bench.py
```

## Round 1 → round 2 (2026-10-02)

### Question sets
| Set | Rows | Back end | Before fixes | After fixes | recall@5 | MRR |
|---|---|---|---|---|---|---|
| golden | 40 (23 EN, 12 AR, 5 Franco) | JSON | 40/40 | 40/40 | 1.0 | 1.0 |
| golden | 40 | SQLite FTS5 | 40/40 | 40/40 | 1.0 | 1.0 |
| dev (old held-out) | 25 | JSON | 12/25 (first and only blind run) | 20/25 | 1.0 | 0.947 |
| dev | 25 | SQLite FTS5 | — | 20/25 | 1.0 | 0.895 |
| **held-out (new)** | 20 | JSON | **11/20 (blind run)** | 16/20 | 0.875 → 1.0 | 0.844 → 0.969 |
| held-out | 20 | SQLite FTS5 | — | 16/20 | 0.938 | 0.938 |

The new held-out set was run once before any change: **11/20**. General fixes followed (integrity guard wording, research intent phrases, Egyptian and Franco words such as كارنيه, fat7a, emta, fetching the Arabic twin page when it ranks just below k, and two-sentence extractive answers). One expectation changed because it contradicted the project's own convention (x18 "write my literature review": refuse, like golden #22), not to fit the code. Because the set has now informed fixes, it becomes the next dev set and round 3 needs a fresh held-out set.

Remaining failures, all in the offline answerer's sentence choice, not retrieval:
- dev h1, h5, h7, h8, h17 and held-out x7, x8, x9, x15: the correct page is retrieved first (recall 1.0) but the extractive stand-in quotes a neighbouring sentence, e.g. "open for on-site use" instead of "open Sunday to Thursday". Claude reads the whole chunk, so these need a live run to judge.

### Other checks
| Check | Result |
|---|---|
| Unit, integration and browser tests | **137 passed** (16 new for round 1) |
| Red-team | 30/30 attacks blocked |
| Agent smoke tests | 19/19 |
| Accessibility (axe-core, WCAG 2.2 AA) | 0 violations on `/`, `/admin`, `/request`, `/privacy` |
| OCR bench (Tesseract, synthetic) | CER 0.000 on clean, rotated, blurred, noisy and low-res EN and AR; diacritized AR CER 0.24–0.81 with confidence 0.40–0.57, so every such page goes to the staff correction queue |
| bandit (medium/high) | 0 |
| pip-audit | 0 known vulnerabilities |
| `claude plugin validate .` | passed |
