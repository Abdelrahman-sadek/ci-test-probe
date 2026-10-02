# Data handling policy (draft for AUC review)

> Draft by the project; **not legal advice**. AUC's legal office must confirm it against Egypt's Personal Data Protection Law (Law No. 151 of 2020) and AUC policy `[VERIFY]`.

## What is processed
| Data | Where | Retention | Protection |
|---|---|---|---|
| Questions | Chat log (`AGENTKIT_LOG`) | 30 days (`AGENTKIT_LOG_RETENTION_DAYS`) | Redacted before storage (emails, national IDs, card and phone numbers); encrypted at rest with Fernet (`AGENTKIT_LOG_KEY`) |
| User identity | Chat log | 30 days | Stored only as an HMAC pseudonym (`AGENTKIT_LOG_SALT`), never as name or email |
| Question text sent to the model provider | Anthropic API | Per provider terms; request **zero data retention** | Redacted first; no account data, no history beyond the last turn |
| Library documents | Index (`data/index.db`) | Until removed | Public pages by default; restricted pages carry an `access` level and are only retrieved for authorised groups |
| Answer cache | Memory only | 1 hour, cleared on restart or index change | Keyed by access level |

## Principles
1. **Minimisation:** no accounts, fines, loans or IDs are needed to answer. The bot refuses to collect them.
2. **Purpose limitation:** logs are used only to improve answers and detect abuse.
3. **Security:** TLS in transit (Caddy), encryption at rest for logs, least-privilege containers, rate limits, audit by `pip-audit` and `bandit`.
4. **Data-subject rights:** a user can ask for deletion; run `agentkit purge-logs --user <id>` (the id is matched through the same pseudonym).
5. **Transparency:** the chat page states that answers come from cited sources, and links to this policy once published.

## Operational checklist
- [ ] Generate and store `AGENTKIT_LOG_KEY`, `AGENTKIT_LOG_SALT` and `AGENTKIT_ADMIN_KEY` in a secret manager, never in git.
- [ ] Schedule `agentkit purge-logs` daily (cron or a container job).
- [ ] Sign the provider data-processing agreement; enable zero data retention.
- [ ] Restrict `/admin` and `/metrics` to staff (SSO groups or campus network).
- [ ] Review the red-team report (`agentkit redteam`) at every release.
