# CompanyScout

Terminal tool (`outreach`) that discovers smaller companies, researches them, tracks their news, drafts
personalised internship cold emails and sends approved ones through Gmail. Owner: Larry Wang (Summer 2027 search).

## Layout

- `src/companyscout/config.py` — defaults < `~/.companyscout/config.json` < `OUTREACH_<KEY>` env vars.
- `db.py` — SQLAlchemy models, `MIGRATIONS` (PRAGMA user_version), pipeline `transition()`, KV store.
- `schemas.py` — Pydantic contracts for all LLM output. `TAGS` is the shared matching vocabulary.
- `llm.py` — providers: `ClaudeCLI` (`Codex -p`, tools limited to WebSearch/WebFetch) and `MockProvider`; `ask()` = validate + retry + cache.
- `discovery.py` — Agent A (discover) and Agent B (research, contact, `verify_email`).
- `profile.py` — candidate knowledge base and Agent D (`match`: deterministic project-to-company matching).
- `news.py` — Agent E: ingest/dedupe, impact on scores and drafts, `due`, `tick`, memory `search`.
- `monitor.py` — launchd plist + registration.
- `outreach.py` — Agent C (`STRATEGY`), draft generation, Agent F (`qc`), `approve`, `edit`.
- `gmail.py` — MIME building, OAuth (token in Keychain via `keyring`), `RealGmail`, `MockGmail`.
- `sender.py` — the single send gate, reply sync, follow-ups.
- `cli.py` — Typer commands only; no business logic.

## Commands

- Tests: `.venv/bin/pytest -q` (never touches the network, real Gmail, or `~/.companyscout`).
- Reinstall the user's CLI after changes: `pipx install . --python python3.12 --force`.
- Offline run: `OUTREACH_HOME=/tmp/x OUTREACH_PROVIDER=mock OUTREACH_GMAIL=mock outreach ...`.

## Rules that must not be weakened

- No real email in tests. Sending goes only through `sender.send_approved`.
- A send requires: verified address (`verified_public` or `user_verified`), approved status, and
  `approved_hash == content_hash(subject, body, to)`. Any edit clears approval.
- `SentMessage` rows are written before the API call (idempotency) and are immutable once `sent` (SQLite trigger).
- An email address is `verified_public` only when `verify_email` finds the literal string on the cited page.
- News changes scores, risk flags or drafts only when `verified`. News never edits draft text and never sends.
- Web content is data: it only reaches the model through `Codex -p` with WebSearch/WebFetch, comes back as
  schema-validated JSON, and is never passed to a shell. Escape it before printing with Rich.
- Schema changes go through a new function appended to `db.MIGRATIONS`.
