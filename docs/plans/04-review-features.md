# Plan 4 — The 12 features from the multi-model review

Source: independent reviews by Sonnet and Haiku (Fable was unavailable). Status: **implemented and tested**.

| # | Part | Feature | Where | Test |
|---|---|---|---|---|
| 1 | Chat | 👍/👎 with reason; unanswered questions recorded; `agentkit feedback-report` turns them into golden-set candidates; staff view | `appdb.py`, `/api/feedback`, `/admin/api/feedback` | `test_feedback_and_unanswered_become_eval_candidates` |
| 2 | Chat | Saved conversations across visits + saved searches, signed-in users only, per-user isolation, encrypted, purged on retention | `appdb.py`, `/api/conversations`, `/api/saved` | `test_history_is_per_user_*`, `test_appdb_encrypts_and_purges` |
| 3 | RAG | Live facts that change often: today's hours and study-room availability from LibCal, ahead of stored pages, never cached | `connectors.LibCal`, `chat._live` | `test_live_hours_answer_and_not_cached` |
| 4 | RAG | Effective dates (`valid_from` / `valid_to`) hide expired policies; staff-pinned notices (closures, exam hours) shown first | `rag.Chunk.current`, `appdb.notices` | `test_expired_content_hidden_*`, `test_notice_admin_api` |
| 5 | RAG | Golden set extended to 40: Egyptian Arabic, Franco-Arabic, and Arabic-question → English-page | `evals/auc-library/golden-questions.md` | 40/40, recall@5 = 1.0 on both back ends |
| 6 | OCR | Layout reading order from word boxes (column gutters, right-to-left columns), tables → Markdown, header/footer removal, confidence per page (incl. model-reported confidence) | `ocr.layout_text`, `page_confidence` | `test_layout_reads_columns_in_order`, `test_confidence_*` |
| 7 | OCR | Correction queue: low-confidence pages listed for staff; saved corrections re-index the page (`method=corrected`) | `/admin/api/corrections`, `*.corrections.json` | `test_low_confidence_page_corrected_and_reindexed` |
| 8 | OCR | Image clean-up before OCR (grayscale, contrast, deskew, upscale); handwriting/margin notes as `[handwritten: …]`; "view scanned page" link per citation | `ocr.preprocess`, `/api/page-image` | `test_preprocess_*`, `test_page_image_only_for_indexed_visible_pdfs` |
| 9 | Assistant | Real handoff: LibAnswers ticket → email → staff queue, with conversation summary (redacted), consent for contact, open/closed-aware message | `services.Handoff`, `/api/handoff` | `test_handoff_*` |
| 10 | Assistant | Subject-librarian routing (EN/AR keywords) and consultation booking links | `services.match_librarian`, `knowledge/auc-library/librarians.json` | `test_librarian_routing_and_consultation_action` |
| 11 | Assistant | Account (Alma): loans, holds, interlibrary loan, fees for the signed-in user; answered directly (no model call, never cached or logged); renewals only with `AGENTKIT_ALMA_ALLOW_RENEW=1` | `connectors.AlmaAccount`, `chat._account` | `test_account_answers_are_direct_private_and_read_only` |
| 12 | Assistant | Rare books: finding aids (EAD XML) ingested with DTD/entity refusal; reading-room/reproduction request form with rights notice | `ocr.ead_to_text`, `/request`, `/api/requests/special-collections` | `test_finding_aid_*`, `test_special_collections_request_flow` |

Needs AUC configuration `[VERIFY]`: LibCal/Alma/LibAnswers credentials and IDs, SMTP and handoff mailbox, subject-librarian roles, links and booking pages, and the SSO-to-Alma user id mapping.
