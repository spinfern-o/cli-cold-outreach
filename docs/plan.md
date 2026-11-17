# CompanyScout implementation plan

Spec: the user's "CompanyScout — Intelligent Cold Outreach CLI" brief (pasted in the
session, condensed below). The brief is the binding authority.

## Global Constraints (condensed spec)

- CLI command `outreach` (Typer + Rich), Python 3.11+, SQLite + SQLAlchemy, Pydantic,
  pytest, official Google API client, launchd. State lives in `~/.companyscout`
  (override `OUTREACH_HOME`); every config key overridable via `OUTREACH_<KEY>` env.
- LLM access goes through a provider adapter: `claude` (local `claude -p`, no API key
  assumed, tools restricted to WebSearch/WebFetch, JSON-schema output) or `mock`.
- Never invent: employee counts carry source + verified date; emails are
  `verified_public` only when the literal address appears on the cited page, else
  `inferred`/`unknown`; sends go only to `verified_public`/`user_verified`.
- Size: 15–50 high, 51–300 highest, 301–750 moderate, 750+ excluded by default.
- Stability class: Strong Evidence / Moderate Evidence / Uncertain / Elevated Risk + confidence.
- Score weights: tech match 25, access 20, stability 20, intern potential 20, timing 10,
  interest 5. Attractiveness (score) is separate from evidence confidence.
- Pipeline: discovered → researched → shortlisted → drafted → reviewed → approved →
  scheduled → sent → replied / followup_pending / closed; plus bounced, rejected,
  opted_out, unavailable.
- Sending: draft-only by default, per-recipient approval, only approved+unchanged
  content, daily cap, min gap, kill switch (`outreach pause`), no duplicate sends,
  immutable sent records, suppression after opt-out/negative, follow-ups pause on reply.
- News agent: shortlist every 12h, tracked 48h, discovery weekly (configurable);
  deduped, timestamped, source-backed, searchable; layoffs → risk flag; significant
  news rescoring + flags unsent drafts for refresh; never overwrites an approved email;
  never auto-sends because of news.
- Emails: 90–140 words, 3 subjects, rationale, one follow-up; exclude Michigan Dining.
- Tests never send real mail. Test command for every task: `.venv/bin/pytest -q`.

## Layout

`src/companyscout/`: `config.py`, `db.py`, `schemas.py`, `scoring.py`, `profile.py`,
`llm.py`, `discovery.py`, `news.py`, `monitor.py`, `outreach.py`, `gmail.py`,
`sender.py`, `cli.py`. Tests in `tests/`.

## Task 1: Scaffold, config, database, migrations, state machine

Files: `pyproject.toml`, `config.py`, `db.py`, `tests/conftest.py`, `tests/test_db.py`.
Produces: `config.load()`/`save()`; `db.session()`; models Company, Contact, NewsItem,
History, Activity, Draft, SentMessage, Reply, Suppression, MonitorRun, KV;
`db.migrate(engine)` (PRAGMA user_version, v1 base, v2 adds `companies.user_rank`);
`db.transition(company, state)` raising `InvalidTransition`; `db.kv_get/kv_set`.
Tests: v1 DB upgrades to v2 keeping rows; illegal transition raises; shortlist flag
persists across sessions; sent row cannot be updated or deleted.

## Task 2: Scoring, filtering, candidate profile and resume matching

Files: `schemas.py`, `scoring.py`, `profile.py`, `tests/test_scoring.py`, `tests/test_profile.py`.
Produces: `scoring.qualify(company, cfg)`, `scoring.score(company, cfg, news=[])` →
(score, breakdown, evidence_confidence), `scoring.filter_companies(q, **filters)`;
`profile.import_profile(text, provider, cfg)`, `profile.match(profile, company, usage)`.
Tests: 100-person stable company outranks 10-person unproven startup; 750+ excluded;
size/stable/industry filters; excluded experiences dropped from profile; matcher picks
CV project for a vision company, FinDash for fintech, and does not always pick the same project.

## Task 3: LLM providers, discovery, research, contact verification

Files: `llm.py`, `discovery.py`, `tests/test_discovery.py`.
Produces: `llm.get_provider(cfg)`, `llm.ask(provider, task, prompt, model_cls, web, session)`
with retry + cache; `discovery.discover(session, provider, cfg, limit)`,
`discovery.research_company(...)`, `discovery.research_all(...)` (parallel batches),
`discovery.verify_email(email, url, fetch)`.
Tests: invalid research JSON rejected by schema; dedupe by canonical domain; retry
recovers after two provider failures; email not on cited page stays `inferred`;
research change is written to History.

## Task 4: News memory agent and launchd monitor

Files: `news.py`, `monitor.py`, `tests/test_news.py`, `tests/test_monitor.py`.
Produces: `news.ingest(session, company, findings, cfg)`, `news.due(session, cfg, now)`,
`news.tick(session, provider, cfg, now)`, `news.search(session, query)`,
`monitor.plist(cfg)`, `monitor.start/stop/status`.
Tests: same story twice stores once; due logic honours 12h/48h and catches up after a
gap; layoffs set Elevated Risk + risk flag; significant news flags an unsent draft and
never changes an approved body; plist contains program path + interval.

## Task 5: Draft generation, quality control, approval

Files: `outreach.py`, `tests/test_outreach.py`.
Produces: `outreach.generate(session, provider, cfg, company)`, `outreach.qc(...)`,
`outreach.approve(session, draft)`, `outreach.edit(session, draft, body, subject)`.
Tests: 60-word draft fails QC; invented number fails QC; near-duplicate of another
draft fails QC; editing an approved draft clears approval; approve refuses unverified contact.

## Task 6: Gmail connector, sending, replies, follow-ups

Files: `gmail.py`, `sender.py`, `tests/test_gmail.py`, `tests/test_sender.py`.
Produces: `gmail.build_message(...)`, `gmail.MockGmail`, `gmail.RealGmail`,
`gmail.connect/disconnect`, `sender.send_approved(...)`, `sender.sync_replies(...)`,
`sender.followups_due(...)`.
Tests: MIME headers + In-Reply-To/References on follow-up; second send to same address
blocked; body changed after approval is not sent; kill switch blocks; daily cap
enforced; retry after crash does not double send; reply marks replied and suppresses
follow-up; opt-out reply suppresses address; bounce marks bounced.

## Task 7: CLI, export, stats, docs

Files: `cli.py`, `README.md`, `CLAUDE.md`, `tests/test_cli.py`.
Produces: every command in the brief §8 plus `resume`, `contact verify`, `config`,
`rate`, `note`, `hide`, `monitor run`.
Tests: end-to-end workflow through the CLI with mock LLM + mock Gmail (init → profile
import → discover → research → shortlist → news refresh → generate → approve → send →
inbox → stats → export).

## Task 8: Install and live run

Install with pipx so `outreach` works from any terminal; run the real workflow with
the `claude` provider against live web data and the mock Gmail provider; record what
was and was not verified.
