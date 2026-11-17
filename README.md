# CompanyScout — `outreach`

A terminal tool that finds smaller companies where a cold email to a technical decision-maker is realistic,
researches them, watches their news, drafts a personalised internship email for each, and sends only what you
approve through your own Gmail account.

Everything is stored locally in `~/.companyscout` (SQLite database, config, logs). Research and writing run
through your installed Claude Code CLI (`claude -p`), so no Anthropic API key is needed.

## Install

```bash
pipx install . --python python3.12
```

```bash
outreach init
```

```bash
outreach doctor
```

`outreach` then works from any terminal, with or without Claude Code open.

## The workflow

```bash
outreach profile import ~/Downloads/Ziheng_Wang_Resume.pdf --docs ~/FinDash/README.md
outreach discover --limit 100          # find companies (deduplicated by domain)
outreach research --all                # one research task per company, in parallel batches
outreach companies --size 15:300 --stable
outreach company 42                    # everything known, with sources
outreach shortlist add 42 17 8
outreach news refresh --shortlist      # fetch and store news now
outreach generate --shortlist          # draft + independent quality review
outreach review                        # approve / edit / pick subject / regenerate / reject
outreach send --approved --limit 5     # draft-only by default: creates Gmail drafts
outreach inbox                         # replies, bounces, opt-outs on managed threads
outreach followups                     # prepare due follow-ups (each needs its own approval)
outreach pipeline && outreach stats
outreach export companies.csv
```

Run `outreach --help` or `outreach <command> --help` for every option.

| Area | Commands |
|---|---|
| Setup | `init`, `doctor`, `config [key] [value]`, `gmail connect/status/disconnect`, `profile import/show/edit` |
| Companies | `discover`, `research`, `companies` (filters: `--size --stable --industry --stack --hiring --location --has-contact --fresh --min-score`, `-i` to browse), `company`, `shortlist [add/remove]`, `hide`, `exclude`, `note`, `rate`, `rank`, `contact set/verify` |
| News | `news [--shortlist]`, `news refresh`, `memory search "…"`, `monitor start/status/stop/run` |
| Email | `generate`, `review`, `edit`, `approve <id>` / `approve --batch N`, `send --approved [--limit N] [--at "YYYY-MM-DD HH:MM"]`, `drafts`, `followups` |
| Tracking | `inbox`, `replies`, `pipeline`, `stats`, `export file.csv|json`, `pause`, `resume`, `close`, `suppress` |

Ids are company ids. `approve 42` approves company 42's current open draft for its recipient.

## How companies are ranked

`Score` (0–100) is attractiveness, **not** a probability of getting an internship. It is a weighted sum:
verified technical match 25, accessible decision-makers 20, stability 20, internship potential 20, timing 10,
your interest 5 (`outreach rate 42 80`). Change weights with `outreach config weights '{...}'`.

`Conf` is reported separately: how well-sourced the research is (employee source, number of sources, freshness,
stability confidence, verified contact). A high score with low confidence means "promising, verify more".

Size bands: 15–50 high, 51–300 highest, 301–750 moderate, above 750 excluded (`size_min`, `size_max`).
Employee counts are estimates; each stores its source URL and the date it was checked.
Stability is one of Strong Evidence / Moderate Evidence / Uncertain / Elevated Risk plus a confidence.

## Honesty rules built into the code

- An email address is `verified_public` only if the exact address is printed on the page cited for it (the tool
  fetches the page itself). Anything else is `inferred` and **cannot be sent to**. If you personally confirm an
  address, `outreach contact verify 42 --email cto@company.com` marks it `user_verified`.
- The candidate profile keeps only technologies that literally appear in your documents, drops entries with no
  evidence, and excludes `exclude_experiences` (Michigan Dining by default).
- Quality control rejects drafts outside 90–140 words, without a Summer 2027 question, with any number that is
  not in your resume or the company research, with stock phrases, or more than 80% similar to another company's
  draft. A second, independent model pass then checks personalisation and resume consistency.
- News affects scores, risk flags and drafts only after the tool has fetched the cited page and found the
  company named on it. Unverified items are stored and labelled but change nothing.

## Sending safety

- **Draft-only by default.** `outreach send --approved` creates Gmail drafts until you run
  `outreach config live_sending true`. Live sends ask for confirmation.
- Every recipient needs an approval. Approval is tied to a hash of the subject, body and address; any edit
  clears it, and a draft whose text no longer matches its approval is refused at send time.
- `daily_send_cap` (default 5) and `min_seconds_between_sends` (default 90).
- One initial email per address and per company, checked against the local record and your Gmail Sent folder.
- Each send is recorded before the API call and looked up by Message-ID after any failure, so a retry or a crash
  cannot send twice. Sent records cannot be edited or deleted.
- Replies stop follow-ups. Opt-outs, declines and bounces suppress the address permanently.
- **Kill switch:** `outreach pause` blocks every send, including scheduled ones, until `outreach resume`.
- News never triggers a send, and never rewrites an approved email: affected drafts are flagged in
  `outreach review` with a suggested new opening.

Please keep volume low and personal. This tool does not buy lists, scrape behind logins, or try to evade spam
filtering, and it should not be used that way.

## Gmail setup (one time, about five minutes)

The tool cannot do this part for you; it needs your Google account.

1. Open <https://console.cloud.google.com/>, create a project (any name).
2. **APIs & Services → Library →** enable **Gmail API**.
3. **APIs & Services → OAuth consent screen →** User type *External*, fill in the app name and your email, add
   yourself under **Test users**. To stop Google expiring the login every 7 days, set publishing status to
   **In production** (no verification is needed for personal use; you will see an "unverified app" notice once).
4. **APIs & Services → Credentials → Create credentials → OAuth client ID →** type **Desktop app**. Download the JSON.
5. Run:

```bash
outreach gmail connect --credentials ~/Downloads/client_secret_XXXX.json
```

A browser window asks you to approve two scopes: `gmail.compose` (create drafts and send) and `gmail.readonly`
(read the threads this tool started, to detect replies and bounces, and one narrow Sent-folder search to avoid
emailing the same person twice). Your password is never seen. The resulting token is stored in the macOS
Keychain under `companyscout-gmail`, not on disk. `outreach gmail disconnect` revokes and deletes it.

## The background news agent

```bash
outreach monitor start
```

This registers a launchd agent (`~/Library/LaunchAgents/com.companyscout.monitor.plist`) that runs
`outreach monitor run` every hour. Each run checks whichever companies are due: shortlisted every 12 hours,
other researched companies every 48 hours, general market news every 48 hours, and new-company discovery weekly
(`shortlist_interval_h`, `tracked_interval_h`, `discovery_interval_h`, `weekly_discovery_limit`).

launchd only runs while the Mac is on, awake and you are logged in. Nothing is lost when it is not: every company
stores its own last-checked time, so whatever became overdue during sleep is picked up on the next run
(shortlist first, at most `monitor_max_per_tick` per run). `outreach monitor status` shows recent runs and how
many scheduled runs were missed.

Background checks use your Claude Code usage. To keep that bounded, non-shortlisted companies that publish an RSS
feed are checked by feed only; web search is used for the shortlist and for companies with no feed.
The agent needs Claude Code to be logged in. If it reports errors, see `~/.companyscout/logs/`.

When significant verified news arrives for a shortlisted company the agent saves it, rescoring the company,
flags any unsent draft for refresh with a suggested opening, adds it to the activity feed (`outreach news`) and
shows a macOS notification. Layoffs set the company to Elevated Risk with a risk flag.

## Configuration

`outreach config` lists every setting; `outreach config <key> <value>` changes one. Any setting can also be
overridden for one command with an environment variable, e.g. `OUTREACH_DAILY_SEND_CAP=2`.
`OUTREACH_HOME` moves the data folder. `claude_model` (default `sonnet`) picks the model used for research.

## Offline / mock mode

```bash
OUTREACH_HOME=/tmp/scout-demo OUTREACH_PROVIDER=mock OUTREACH_GMAIL=mock outreach discover --limit 3
```

The mock provider returns three fictional `.example` companies and the mock mailbox is a local JSON file, so the
whole pipeline can be exercised without the network.

## Development

```bash
python3.12 -m venv .venv && .venv/bin/pip install -e '.[dev]' && .venv/bin/pytest -q
```

See `CLAUDE.md` for the module map and the rules that must not be weakened.
