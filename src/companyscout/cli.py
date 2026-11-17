"""`outreach` — company discovery and personalised cold-outreach from the terminal."""
import csv
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from . import config, db, discovery, gmail, llm, monitor, news, outreach, profile as profile_mod, scoring, sender

app = typer.Typer(no_args_is_help=True, help="Discover, research, shortlist and contact smaller companies for a Summer 2027 internship.")
gmail_app = typer.Typer(no_args_is_help=True, help="Connect or disconnect your Gmail account (OAuth, token kept in the macOS Keychain).")
profile_app = typer.Typer(no_args_is_help=True, help="Candidate knowledge base built from your resume and project docs.")
shortlist_app = typer.Typer(invoke_without_command=True, help="Show the shortlist, or add/remove companies.")
news_app = typer.Typer(invoke_without_command=True, help="Stored company news and the activity feed.")
memory_app = typer.Typer(no_args_is_help=True, help="Search everything the news agent has stored.")
monitor_app = typer.Typer(no_args_is_help=True, help="Background news agent (launchd).")
contact_app = typer.Typer(no_args_is_help=True, help="Set or verify a company's contact.")
for name, sub in [("gmail", gmail_app), ("profile", profile_app), ("shortlist", shortlist_app), ("news", news_app),
                  ("memory", memory_app), ("monitor", monitor_app), ("contact", contact_app)]:
    app.add_typer(sub, name=name)

console = Console()
cfg: dict = {}
_session = None


@app.callback()
def _main():
    global console, cfg, _session
    console, cfg, _session = Console(), config.load(), None  # fresh per invocation: OUTREACH_HOME may differ
    config.setup_logging()


def open_editor(text: str, extension: str = ".txt") -> Optional[str]:
    """Edit text in $VISUAL/$EDITOR (nano by default). Returns the new text, or None if the editor failed."""
    with tempfile.NamedTemporaryFile("w", suffix=extension, delete=False) as f:
        f.write(text)
    try:
        editor = shlex.split(os.environ.get("VISUAL") or os.environ.get("EDITOR") or "nano")
        return Path(f.name).read_text() if subprocess.run([*editor, f.name]).returncode == 0 else None
    finally:
        os.unlink(f.name)


def S():
    global _session
    if _session is None:
        _session = db.connect()
    return _session


def fail(message: str):
    console.print(f"[red]{escape(message)}[/red]")
    raise typer.Exit(1)


def company_or_fail(cid: int):
    c = S().get(db.Company, cid)
    return c if c else fail(f"No company with id {cid}. See `outreach companies`.")


def need_profile():
    p = profile_mod.load_profile(S())
    return p if p else fail("No candidate profile yet. Run: outreach profile import ./resume.pdf")


def fmt(dt) -> str:
    return dt.strftime("%Y-%m-%d %H:%M") if dt else "—"


def current_draft(cid: int):
    """The draft awaiting action for a company: a pending follow-up first, otherwise the latest initial draft."""
    q = S().query(db.Draft).filter(db.Draft.company_id == cid, db.Draft.status.in_(("drafted", "reviewed", "approved", "scheduled")))
    return q.order_by((db.Draft.kind == "followup").desc(), db.Draft.id.desc()).first()


def company_table(rows, title="Companies") -> Table:
    t = Table(title=f"{title} ({len(rows)})")
    for col in ("ID", "Name", "Industry", "Emp", "Stability", "Hiring", "Score", "Conf", "State", "★", "Contact", "Lead project"):
        t.add_column(col)
    for c in rows:
        k = c.contacts[0] if c.contacts else None
        t.add_row(str(c.id), escape(c.name), escape(c.industry or "—"), str(c.employee_count or "?"), c.stability or "—",
                  c.hiring or "—", f"{c.score:.0f}" if c.score is not None else "—",
                  f"{c.evidence_confidence:.0%}" if c.evidence_confidence is not None else "—",
                  c.state + (" ⚠" if c.risk_flags else ""), "★" if c.shortlisted else "",
                  escape(f"{k.name or '?'} ({k.email_status})") if k else "—", escape(c.primary_project or "—"))
    return t


# ---------------------------------------------------------------- setup

@app.command()
def init():
    """Create the data folder, database and config."""
    S()
    found = shutil.which("claude")
    if found and not (config.home() / "config.json").exists():
        config.save({"claude_path": found})
    elif not (config.home() / "config.json").exists():
        config.save({})
    console.print(f"[green]Ready.[/green] Data lives in {config.home()}")
    console.print("Next: outreach profile import ./resume.pdf  →  outreach discover --limit 100  →  outreach research --all")


@app.command()
def doctor():
    """Check every dependency and connection."""
    checks = [("Data folder", True, str(config.home())), ("Database", True, f"schema v{len(db.MIGRATIONS)}")]
    S()
    if cfg["provider"] == "mock":
        checks.append(("LLM provider", True, "mock (offline fixtures)"))
    else:
        try:
            v = subprocess.run([cfg["claude_path"], "--version"], capture_output=True, text=True, timeout=20)
            checks.append(("Claude Code CLI", v.returncode == 0, v.stdout.strip() or v.stderr.strip()[:80]))
        except (OSError, subprocess.TimeoutExpired) as e:
            checks.append(("Claude Code CLI", False, f"{e} — set claude_path with `outreach config claude_path /path/to/claude`"))
    checks.append(("Candidate profile", profile_mod.load_profile(S()) is not None, "outreach profile import ./resume.pdf"))
    if cfg["gmail"] == "mock":
        checks.append(("Gmail", True, "mock mailbox (nothing leaves this Mac)"))
    else:
        try:
            checks.append(("Gmail", True, f"connected as {gmail.get_client(cfg).address}"))
        except Exception as e:
            checks.append(("Gmail", False, str(e)[:120]))
    checks.append(("Background monitor", monitor.loaded(), "outreach monitor start"))
    checks.append(("Sending mode", True, "LIVE sending enabled" if cfg["live_sending"] else "draft-only (default)"))
    checks.append(("Kill switch", True, "PAUSED — no sends" if db.kv_get(S(), "paused", False) else "not engaged"))
    t = Table(title="outreach doctor")
    for col in ("Check", "OK", "Detail"):
        t.add_column(col)
    for name, ok, detail in checks:
        t.add_row(name, "[green]✓[/green]" if ok else "[yellow]✗[/yellow]", escape(detail))
    console.print(t)


@app.command("config")
def config_cmd(key: Optional[str] = typer.Argument(None), value: Optional[str] = typer.Argument(None)):
    """Show settings, or set one: outreach config daily_send_cap 3"""
    if key is None:
        for k in config.DEFAULTS:
            console.print(f"{k} = {json.dumps(cfg[k])}")
        return
    if key not in config.DEFAULTS:
        fail(f"Unknown setting {key!r}. Run `outreach config` to list them.")
    if value is None:
        return console.print(f"{key} = {json.dumps(cfg[key])}")
    try:
        parsed = json.loads(value)
    except ValueError:
        parsed = value
    config.save({key: parsed})
    console.print(f"{key} = {json.dumps(parsed)}")
    if key == "live_sending" and parsed:
        console.print("[yellow]Live sending is ON. `outreach send --approved` will now send real email. `outreach pause` stops everything.[/yellow]")


@gmail_app.command("connect")
def gmail_connect(credentials: Path = typer.Option(None, help="OAuth client JSON downloaded from Google Cloud (Desktop app).")):
    """Authorize Gmail in your browser. Your password is never seen or stored by this tool."""
    credentials = credentials or config.home() / "client_secret.json"
    if not credentials.exists():
        fail(f"OAuth client file not found at {credentials}. See README 'Gmail setup' to create one, then pass --credentials.")
    gmail.connect(cfg, credentials)
    console.print(f"[green]Connected as {gmail.get_client(cfg).address}.[/green] Token stored in the macOS Keychain ({gmail.SERVICE}).")


@gmail_app.command("status")
def gmail_status():
    """Show which account is connected."""
    try:
        console.print(f"Connected as {gmail.get_client(cfg).address}")
    except Exception as e:
        fail(str(e))


@gmail_app.command("disconnect")
def gmail_disconnect():
    """Revoke the token and delete it from the Keychain."""
    console.print("Disconnected and token removed." if gmail.disconnect() else "No Gmail account was connected.")


@profile_app.command("import")
def profile_import(resume: Path, docs: list[Path] = typer.Option([], "--docs", help="Extra project docs (README, write-ups). Repeatable.")):
    """Build the candidate knowledge base from a resume (PDF or text) plus optional project docs."""
    parts = [profile_mod.extract_text(resume)]
    for d in docs:
        parts.append(f"\n--- {d.name} ---\n{profile_mod.extract_text(d)[:8000]}")
    parts.append("\nCandidate-stated credentials: " + "; ".join(cfg["credentials"]))
    with console.status("Reading documents…"):
        p = profile_mod.import_profile(S(), llm.get_provider(cfg), cfg, "\n".join(parts))
    _show_profile(p)


def _show_profile(p):
    console.print(Panel(escape(f"{p.name} — {p.headline}\n{p.education}\nCredentials: {'; '.join(list(p.credentials) + cfg['credentials'])}"),
                        title="Candidate"))
    t = Table(title="Verified projects and experience")
    for col in ("Name", "Summary", "Technologies", "Tags"):
        t.add_column(col)
    for x in p.projects:
        t.add_row(escape(x.name), escape(x.summary), escape(", ".join(x.technologies)), ", ".join(x.tags))
    console.print(t)


@profile_app.command("show")
def profile_show():
    """Show the stored knowledge base."""
    _show_profile(need_profile())


@profile_app.command("edit")
def profile_edit():
    """Edit the knowledge base JSON in $EDITOR."""
    p = need_profile()
    edited = open_editor(json.dumps(p.model_dump(exclude={"source_text"}), indent=2), extension=".json")
    if edited:
        new = profile_mod.CandidateProfile.model_validate({**json.loads(edited), "source_text": p.source_text})
        db.kv_set(S(), "profile", new.model_dump())
        console.print("Profile saved.")


# ---------------------------------------------------------------- discovery and research

@app.command()
def discover(limit: int = typer.Option(25, help="How many new companies to add.")):
    """Find new companies across many industry segments (deduplicated by domain)."""
    with console.status("Searching…") as status:
        added = discovery.discover(S(), llm.get_provider(cfg), cfg, limit, progress=lambda n: status.update(f"Searching… {n} found"))
    total = S().query(db.Company).count()
    console.print(f"Added {added} new companies ({total} tracked). Next: outreach research --all")


@app.command()
def research(ids: Optional[list[int]] = typer.Argument(None), all_: bool = typer.Option(False, "--all", help="Every company not yet researched."),
             limit: int = typer.Option(0, help="Cap how many to research."), force: bool = typer.Option(False, help="Re-research even if fresh.")):
    """Research companies in parallel: product, stability, stack, hiring, best contact, resume match."""
    if ids:
        targets = [company_or_fail(i) for i in ids]
    elif all_:
        q = S().query(db.Company).filter(db.Company.hidden.isnot(True))
        targets = [c for c in q if force or (c.researched_at is None and not c.excluded_reason)]
    else:
        fail("Give company ids or --all.")
    targets = targets[:limit] if limit else targets
    if not targets:
        return console.print("Nothing to research.")
    console.print(f"Researching {len(targets)} companies, {cfg['research_parallel']} at a time…")

    def progress(company, ok):
        console.print(f"  {'[green]✓[/green]' if ok else '[red]✗ failed (left unchanged)[/red]'} {escape(company.name)}")

    done, failed = discovery.research_all(S(), llm.get_provider(cfg), cfg, targets, profile_mod.load_profile(S()), progress=progress)
    console.print(f"Researched {done}, failed {failed}. Next: outreach companies --stable")


@app.command()
def companies(size: Optional[str] = typer.Option(None, help="Employee range, e.g. 15:300"), stable: bool = False,
              industry: Optional[str] = None, stack: Optional[str] = None, hiring: Optional[str] = typer.Option(None, help="intern_evidence | hiring_eng | unknown"),
              location: Optional[str] = None, has_contact: bool = typer.Option(False, help="Only companies with a verified email."),
              fresh: Optional[int] = typer.Option(None, help="Researched within N days."), min_score: Optional[float] = None,
              all_: bool = typer.Option(False, "--all", help="Include hidden and excluded."), limit: int = 50,
              interactive: bool = typer.Option(False, "-i", help="Browse: open, shortlist and hide from a prompt.")):
    """List tracked companies, best opportunity first."""
    lo_hi = tuple(int(x) for x in size.split(":")) if size else None
    while True:
        rows = scoring.filter_companies(S(), size=lo_hi, industry=industry, stable=stable, stack=stack, hiring=hiring, location=location,
                                        has_contact=has_contact, fresh_days=fresh, min_score=min_score, include_hidden=all_)[:limit]
        console.print(company_table(rows))
        console.print("[dim]Score = attractiveness (0-100, not a probability). Conf = how well-sourced the research is.[/dim]")
        if not interactive:
            return
        cmd = typer.prompt("id = open · s <id> = shortlist · h <id> = hide · q = quit").split()
        if not cmd or cmd[0] == "q":
            return
        if cmd[0] in ("s", "h") and len(cmd) == 2 and cmd[1].isdigit():
            c = company_or_fail(int(cmd[1]))
            if cmd[0] == "s":
                _shortlist(c, True)
            else:
                c.hidden = True
            S().commit()
        elif cmd[0].isdigit():
            company(int(cmd[0]))


@app.command()
def company(cid: int):
    """Everything known about one company, with sources."""
    c = company_or_fail(cid)
    r = c.research or {}
    lines = [f"[bold]{escape(c.name)}[/bold]  {escape(c.website or c.domain)}   state: {c.state}{'  ★ shortlisted' if c.shortlisted else ''}",
             escape(c.description or ""),
             f"Industry: {escape(c.industry or '—')} · Location: {escape(c.location or '—')}{' · remote' if c.remote else ''} · Founded: {c.founded_year or '—'}",
             f"Employees: {c.employee_count or 'unknown'} (estimate; source: {escape(c.employee_source or 'none')}; checked: {fmt(c.employee_verified_at)})",
             f"Stability: {c.stability or '—'} (confidence {c.stability_confidence or 0:.0%}) — {escape('; '.join(r.get('stability_evidence', [])))}",
             f"Hiring: {c.hiring or '—'} · Openings: {escape(', '.join(r.get('engineering_openings', [])) or 'none found')} · Interns: {escape(r.get('internship_evidence') or 'no evidence')}",
             f"Stack: {escape(', '.join(c.stack or []) or '—')} · Tags: {', '.join(c.tags or []) or '—'}",
             f"Engineering presence: {escape(r.get('engineering_presence') or '—')}",
             f"[bold]Score {c.score or 0:.0f}/100[/bold] (attractiveness, not a probability) · evidence confidence {c.evidence_confidence or 0:.0%}",
             "  " + " · ".join(f"{k} {v:.0f}" for k, v in (c.breakdown or {}).items()),
             f"Lead project: {escape(c.primary_project or '—')} — {escape(c.match_rationale or '')}"]
    if c.risk_flags:
        lines.append("[red]Risk flags:[/red] " + escape("; ".join(c.risk_flags)))
    if c.excluded_reason:
        lines.append(f"[yellow]Excluded:[/yellow] {escape(c.excluded_reason)}")
    for k in c.contacts:
        lines.append(f"Contact: {escape(k.name or '?')}, {escape(k.title or '?')} — {escape(k.email or 'no public email found')} "
                     f"[{k.email_status}] {escape(k.email_source_url or k.profile_url or '')}")
    if c.notes:
        lines.append(f"Notes: {escape(c.notes)}")
    lines.append(f"Researched: {fmt(c.researched_at)} · News checked: {fmt(c.news_checked_at)}")
    lines.append("Sources: " + escape(", ".join(c.sources or []) or "none"))
    console.print(Panel("\n".join(lines), title=f"Company {c.id}"))
    items = S().query(db.NewsItem).filter_by(company_id=c.id).order_by(db.NewsItem.fetched_at.desc()).limit(8).all()
    if items:
        _news_table(items, "Recent news")
    history = S().query(db.History).filter_by(company_id=c.id).order_by(db.History.id.desc()).limit(8).all()
    for h in history:
        console.print(f"[dim]{fmt(h.at)} {h.field}: {escape(str(h.old))} → {escape(str(h.new))} ({escape(h.source or '')})[/dim]")
    d = current_draft(c.id)
    if d:
        console.print(f"Draft #{d.id} ({d.kind}) is {d.status}{' — needs refresh' if d.needs_refresh else ''}. See `outreach review`.")


def _shortlist(c, on: bool):
    c.shortlisted = on
    if on and c.state == "researched":
        db.transition(c, "shortlisted")
    elif not on and c.state == "shortlisted":
        db.transition(c, "researched")


@shortlist_app.callback()
def shortlist_list(ctx: typer.Context):
    if ctx.invoked_subcommand is None:
        console.print(company_table(scoring.filter_companies(S(), shortlist=True), "Shortlist"))


@shortlist_app.command("add")
def shortlist_add(ids: list[int]):
    """Shortlist companies (checked for news every 12h by the monitor)."""
    for i in ids:
        c = company_or_fail(i)
        if c.researched_at is None:
            fail(f"{c.name} has not been researched yet. Run: outreach research {i}")
        _shortlist(c, True)
        console.print(f"★ {escape(c.name)}")
    S().commit()


@shortlist_app.command("remove")
def shortlist_remove(ids: list[int]):
    """Remove companies from the shortlist."""
    for i in ids:
        _shortlist(company_or_fail(i), False)
    S().commit()
    console.print("Removed.")


@app.command()
def hide(cid: int, undo: bool = False):
    """Hide a company from lists (or --undo)."""
    company_or_fail(cid).hidden = not undo
    S().commit()


@app.command()
def exclude(cid: int, reason: str = typer.Argument("not a fit")):
    """Exclude a company from discovery results, monitoring and outreach."""
    c = company_or_fail(cid)
    c.excluded_reason, c.shortlisted = f"user: {reason}", False
    S().commit()


@app.command()
def note(cid: int, text: str):
    """Attach a note to a company."""
    c = company_or_fail(cid)
    c.notes = f"{c.notes}\n{text}" if c.notes else text
    S().commit()


@app.command()
def rate(cid: int, interest: int = typer.Argument(..., min=0, max=100)):
    """Set your personal interest (0-100); feeds the 5% interest weight."""
    c = company_or_fail(cid)
    c.interest = interest
    scoring.apply(S(), c, cfg)
    S().commit()
    console.print(f"{escape(c.name)}: score {c.score:.0f}")


@app.command()
def rank(cid: int, position: int = typer.Argument(..., help="1 = top. 0 clears your manual rank.")):
    """Pin a company to a position you choose; ranked companies list before scored ones."""
    company_or_fail(cid).user_rank = position or None
    S().commit()


@contact_app.command("set")
def contact_set(cid: int, name: Optional[str] = None, title: Optional[str] = None, email: Optional[str] = None,
                source: Optional[str] = typer.Option(None, help="Public page where the address is printed.")):
    """Record a contact. The address is marked verified only if it is printed on the --source page."""
    c = company_or_fail(cid)
    if not c.contacts:
        c.contacts.append(db.Contact())
    k = c.contacts[0]
    k.name, k.title = name or k.name, title or k.title
    if email:
        k.email, k.email_source_url = email, source
        k.email_status = discovery.verify_email(email, source)
        k.verified_at = db.now() if k.email_status == "verified_public" else None
    S().commit()
    console.print(f"{escape(k.name or '?')} — {escape(k.email or 'no email')} [{k.email_status}]")


@contact_app.command("verify")
def contact_verify(cid: int, email: Optional[str] = None, yes: bool = False):
    """Confirm, as the user, that an address is real and publicly listed for professional contact."""
    c = company_or_fail(cid)
    if not c.contacts:
        c.contacts.append(db.Contact())
    k = c.contacts[0]
    email = email or k.email
    if not email:
        fail("No address on file. Pass --email.")
    if not yes and not typer.confirm(f"You confirm {email} is a real, publicly listed professional address (not a guess)?"):
        raise typer.Exit(1)
    k.email, k.email_status, k.verified_at = email, "user_verified", db.now()
    scoring.apply(S(), c, cfg)
    S().commit()
    console.print(f"{escape(email)} marked user_verified for {escape(c.name)}.")


# ---------------------------------------------------------------- news and monitor

def _news_table(items, title):
    t = Table(title=title)
    for col in ("Date", "Company", "Category", "Sig", "Verified", "Headline", "Source"):
        t.add_column(col)
    for n in items:
        t.add_row(fmt(n.published_at or n.fetched_at)[:10], escape(n.company.name if n.company else "market"), n.category or "",
                  str(n.significance or 0), "✓" if n.verified else "unverified", escape(n.title), escape(n.url or ""))
    console.print(t)


@news_app.callback()
def news_list(ctx: typer.Context, shortlist: bool = typer.Option(False, "--shortlist"), limit: int = 30):
    if ctx.invoked_subcommand is not None:
        return
    q = S().query(db.NewsItem).order_by(db.NewsItem.fetched_at.desc())
    items = [n for n in q if not shortlist or (n.company and n.company.shortlisted)][:limit]
    _news_table(items, "Shortlist news" if shortlist else "News memory")
    feed = S().query(db.Activity).order_by(db.Activity.id.desc()).limit(10).all()
    if feed:
        console.print("[bold]Activity feed[/bold]")
        for a in feed:
            console.print(f"  {fmt(a.at)} [{a.kind}] {escape(a.message)}")
    stale = S().query(db.Draft).filter_by(needs_refresh=True).filter(db.Draft.status.in_(news.UNSENT)).count()
    if stale:
        console.print(f"[yellow]{stale} unsent draft(s) flagged for refresh — see `outreach review`.[/yellow]")


@news_app.command("refresh")
def news_refresh(ids: Optional[list[int]] = typer.Argument(None), shortlist: bool = typer.Option(False, "--shortlist"),
                 all_: bool = typer.Option(False, "--all", help="Every tracked company, due or not.")):
    """Check for news now. Default: whatever is due. --shortlist: the whole shortlist."""
    if ids:
        targets = [company_or_fail(i) for i in ids]
    elif shortlist:
        targets = scoring.filter_companies(S(), shortlist=True)
    elif all_:
        targets = [c for c in scoring.filter_companies(S()) if c.state != "discovered"]
    else:
        targets = news.due(S(), cfg)
    provider, p, found = llm.get_provider(cfg), profile_mod.load_profile(S()), 0
    for c in targets:
        with console.status(f"Checking {c.name}…"):
            n = news.check_company(S(), provider, cfg, c, p)
        found += n
        console.print(f"  {escape(c.name)}: {n} new")
    console.print(f"Checked {len(targets)} companies, {found} new items. See `outreach news`.")


@memory_app.command("search")
def memory_search(query: str, limit: int = 20):
    """Search stored news and company profiles."""
    t = Table(title=f"Memory: {query}")
    for col in ("Kind", "Company", "Date", "Title", "Source"):
        t.add_column(col)
    for h in news.search(S(), query, limit):
        t.add_row(h["kind"], escape(h["company"]), fmt(h["date"])[:10], escape(h["title"]), escape(h["url"] or ""))
    console.print(t)


@monitor_app.command("start")
def monitor_start():
    """Register the background agent with launchd."""
    exe = shutil.which("outreach") or str(Path(sys.argv[0]).resolve())
    path = monitor.start(cfg, exe)
    console.print(f"[green]Monitor registered[/green] ({path}). It wakes every {cfg['monitor_tick_s'] // 60} min and checks whatever is due: "
                  f"shortlist every {cfg['shortlist_interval_h']}h, other companies every {cfg['tracked_interval_h']}h, discovery every "
                  f"{cfg['discovery_interval_h']}h.")
    console.print("It runs only while this Mac is on, awake and you are logged in. Checks missed during sleep are not lost: "
                  "each company keeps its own last-checked time, so overdue ones are picked up on the next wake.")


@monitor_app.command("stop")
def monitor_stop():
    """Unregister the background agent."""
    monitor.stop()
    console.print("Monitor stopped and unregistered.")


@monitor_app.command("status")
def monitor_status():
    """Is the agent registered, when did it last run, what is due."""
    console.print(f"launchd agent: {'[green]loaded[/green]' if monitor.loaded() else '[yellow]not loaded[/yellow] (outreach monitor start)'}")
    console.print(f"Due now: {len(news.due(S(), cfg))} companies")
    t = Table(title="Recent runs")
    for col in ("Started", "Finished", "Checked", "New items", "Missed ticks", "Error"):
        t.add_column(col)
    for r in S().query(db.MonitorRun).order_by(db.MonitorRun.id.desc()).limit(8):
        t.add_row(fmt(r.started_at), fmt(r.finished_at), str(r.checked), str(r.new_items), str(r.missed_ticks), escape(r.error or ""))
    console.print(t)


@monitor_app.command("run")
def monitor_run():
    """One monitoring pass (what launchd calls). Never sends anything that was not explicitly approved and scheduled."""
    run = news.tick(S(), llm.get_provider(cfg), cfg, profile=profile_mod.load_profile(S()))
    console.print(f"Checked {run.checked}, {run.new_items} new items, {run.missed_ticks} missed ticks{', error: ' + run.error if run.error else ''}")
    try:
        client = gmail.get_client(cfg)
        replies = sender.sync_replies(S(), client, cfg)
        sender.prepare_followups(S(), cfg)
        if replies:
            news.send_notification("CompanyScout", f"{len(replies)} new reply(ies) — outreach inbox")
        if cfg["live_sending"] and not db.kv_get(S(), "paused", False):
            sender.send_approved(S(), client, cfg, cfg["daily_send_cap"], only_scheduled=True)
    except gmail.GmailError:
        pass  # Gmail not connected: news monitoring still runs


# ---------------------------------------------------------------- email workflow

@app.command()
def generate(ids: Optional[list[int]] = typer.Argument(None), shortlist: bool = typer.Option(False, "--shortlist"),
             force: bool = typer.Option(False, help="Regenerate, replacing an existing (even approved) draft.")):
    """Draft personalised emails and run them through quality control."""
    p, provider = need_profile(), llm.get_provider(cfg)
    if ids:
        targets = [company_or_fail(i) for i in ids]
    elif shortlist:
        targets = [c for c in scoring.filter_companies(S(), shortlist=True) if c.state in ("shortlisted", "drafted", "reviewed") or force]
        targets = [c for c in targets if force or current_draft(c.id) is None or current_draft(c.id).needs_refresh]
    else:
        fail("Give company ids or --shortlist.")
    for c in targets:
        if c.researched_at is None:
            console.print(f"  [yellow]skip[/yellow] {escape(c.name)}: not researched")
            continue
        if c.risk_flags:
            console.print(f"  [red]warning[/red] {escape(c.name)} has risk flags: {escape('; '.join(c.risk_flags))}")
        try:
            with console.status(f"Drafting for {c.name}…"):
                d = outreach.generate(S(), provider, cfg, c, p, force=force)
        except (outreach.ApprovalError, llm.ProviderError) as e:
            console.print(f"  [yellow]skip[/yellow] {escape(c.name)}: {escape(str(e))}")
            continue
        verdict = "[green]passed QC[/green]" if d.qc["passed"] else "[red]rejected by QC[/red]: " + escape("; ".join(d.qc["issues"]))
        console.print(f"  {escape(c.name)}: draft #{d.id} {verdict}")
    console.print("Next: outreach review")


def _show_draft(d):
    k, qc = d.contact, d.qc or {}
    head = [f"[bold]{escape(d.company.name)}[/bold] · draft #{d.id} ({d.kind}) · status: {d.status}",
            f"To: {escape(k.name or '?') if k else 'no contact'} <{escape(k.email or 'no address') if k else '—'}> [{k.email_status if k else 'unknown'}]",
            "Subjects: " + " | ".join(f"{'▶ ' if s == d.subject else ''}{i + 1}. {escape(s)}" for i, s in enumerate(d.subjects or [d.subject]))]
    if qc:
        head.append(f"QC: {'[green]pass[/green]' if qc.get('passed') else '[red]reject[/red]'} · {qc.get('words')} words · scores {qc.get('scores') or {}}")
        for issue in qc.get("issues", []):
            head.append(f"  [red]✗ {escape(issue)}[/red]")
        for w in qc.get("warnings", []):
            head.append(f"  [yellow]! {escape(w)}[/yellow]")
    if d.needs_refresh:
        head.append(f"[yellow]Flagged for refresh by new information:[/yellow]\n{escape(d.refresh_suggestion or '')}")
    if d.company.risk_flags:
        head.append("[red]Risk flags:[/red] " + escape("; ".join(d.company.risk_flags)))
    console.print(Panel("\n".join(head) + "\n\n" + escape(d.body or ""), title="Draft"))
    if d.rationale:
        console.print(f"[dim]Why this email: {escape(d.rationale)}[/dim]")
    if d.followup_body:
        console.print(f"[dim]Follow-up (sent only after separate approval): {escape(d.followup_body)}[/dim]")


@app.command()
def review(all_: bool = typer.Option(False, "--all", help="Include already-approved drafts.")):
    """Step through drafts: approve, edit in $EDITOR, pick a subject, regenerate or reject."""
    statuses = ("drafted", "reviewed", "approved", "scheduled") if all_ else ("drafted", "reviewed")
    queue = [d for d in S().query(db.Draft).order_by(db.Draft.id) if d.status in statuses or (d.needs_refresh and d.status in news.UNSENT)]
    if not queue:
        return console.print("No drafts waiting for review.")
    p = need_profile()
    for d in queue:
        while True:
            _show_draft(d)
            choice = typer.prompt("[a]pprove  [e]dit  [1-3] subject  [g] regenerate  [r]eject  [n]ext  [q]uit", default="n").strip().lower()
            if choice == "q":
                return
            if choice == "n":
                break
            if choice == "a":
                try:
                    outreach.approve(S(), d)
                    console.print(f"[green]Approved for {escape(d.contact.email)}.[/green] Only this exact text can be sent.")
                    break
                except outreach.ApprovalError as e:
                    console.print(f"[red]{escape(str(e))}[/red]")
            elif choice == "e":
                edited = open_editor(d.body)
                if edited and outreach.edit(S(), d, body=edited):
                    outreach.qc(S(), llm.get_provider(cfg), cfg, d, p)
                    console.print("[yellow]Draft changed — approval cleared; it must be approved again.[/yellow]")
            elif choice in ("1", "2", "3") and int(choice) <= len(d.subjects or []):
                outreach.edit(S(), d, subject=d.subjects[int(choice) - 1])
                outreach.qc(S(), None, cfg, d, p)
            elif choice == "g" and d.kind == "initial":
                if d.status in ("approved", "scheduled") and not typer.confirm("This replaces an approved email. Continue?"):
                    continue
                try:
                    d = outreach.generate(S(), llm.get_provider(cfg), cfg, d.company, p, force=True)
                except llm.ProviderError as e:
                    console.print(f"[red]{escape(str(e))}[/red]")
            elif choice == "r":
                d.status = "rejected"
                if d.kind == "initial" and d.company.state in ("drafted", "reviewed", "approved"):
                    db.transition(d.company, "shortlisted" if d.company.state != "approved" else "drafted")
                S().commit()
                break


@app.command()
def edit(cid: int):
    """Edit a company's current draft in $EDITOR (clears any approval)."""
    d = current_draft(cid) or fail("No open draft for that company.")
    edited = open_editor(d.body)
    if edited and outreach.edit(S(), d, body=edited):
        outreach.qc(S(), llm.get_provider(cfg), cfg, d, need_profile())
        console.print("Saved. Approval cleared; review and approve again.")


@app.command()
def approve(cid: Optional[int] = typer.Argument(None, help="Company id."), batch: Optional[int] = typer.Option(None, help="Approve up to N QC-passed drafts."),
            force: bool = typer.Option(False, help="Approve a draft that failed QC."), yes: bool = False):
    """Approve one recipient's draft, or a reviewed batch after seeing the list."""
    if cid is not None:
        d = current_draft(cid) or fail("No open draft for that company. Run `outreach generate`.")
        try:
            outreach.approve(S(), d, force=force)
        except outreach.ApprovalError as e:
            fail(str(e))
        if d.needs_refresh:
            console.print("[yellow]Note: newer information exists for this company (see `outreach review`).[/yellow]")
        return console.print(f"[green]Approved[/green] draft #{d.id} for {escape(d.contact.email)}.")
    if batch is None:
        fail("Give a company id or --batch N.")
    ready = [d for d in S().query(db.Draft).filter_by(status="reviewed").order_by(db.Draft.id)
             if d.contact and d.contact.email_status in outreach.VERIFIED and not d.needs_refresh][:batch]
    if not ready:
        return console.print("No QC-passed drafts with a verified recipient are waiting.")
    for d in ready:
        console.print(f"  #{d.id} {escape(d.company.name)} → {escape(d.contact.email)} · {escape(d.subject)}")
    if not yes and not typer.confirm(f"Approve these {len(ready)} drafts exactly as written?"):
        raise typer.Exit(1)
    for d in ready:
        outreach.approve(S(), d)
    console.print(f"[green]Approved {len(ready)}.[/green]")


@app.command()
def send(approved: bool = typer.Option(False, "--approved", help="Required: only approved drafts are ever sent."), limit: int = 5,
         at: Optional[str] = typer.Option(None, help='Schedule instead of sending now, local time: "2026-11-18 09:00"'), yes: bool = False):
    """Send approved emails (or create Gmail drafts while in draft-only mode)."""
    if not approved:
        fail("Pass --approved. Only approved, unchanged drafts can be sent.")
    if db.kv_get(S(), "paused", False):
        fail("Sending is paused (kill switch). Run `outreach resume` to re-enable.")
    ready = S().query(db.Draft).filter_by(status="approved").order_by(db.Draft.id).limit(limit).all()
    if at:
        when = datetime.fromisoformat(at).astimezone().astimezone(tz=None)
        utc = datetime.utcfromtimestamp(when.timestamp())
        for d in ready:
            sender.schedule(S(), d, utc)
        return console.print(f"Scheduled {len(ready)} approved draft(s) for {at}. The monitor sends them when due if live sending is on.")
    try:
        client = gmail.get_client(cfg)
    except gmail.GmailError as e:
        fail(str(e))
    if cfg["live_sending"]:
        for d in ready:
            console.print(f"  #{d.id} {escape(d.company.name)} → {escape(d.contact.email if d.contact else '?')} · {escape(d.subject)}")
        if ready and not yes and not typer.confirm(f"Send up to {len(ready)} REAL email(s) from {client.address}?"):
            raise typer.Exit(1)
    else:
        console.print("[yellow]draft-only mode:[/yellow] creating Gmail drafts, sending nothing. Enable real sending with "
                      "`outreach config live_sending true`.")
    try:
        results = sender.send_approved(S(), client, cfg, limit)
    except sender.SendBlocked as e:
        fail(str(e))
    for d, outcome in results:
        console.print(f"  #{d.id} {escape(d.company.name)}: {escape(outcome)}")
    if not results:
        console.print("Nothing approved to send. See `outreach review`.")


@app.command()
def drafts():
    """All drafts and their status."""
    t = Table(title="Drafts")
    for col in ("ID", "Company", "Kind", "Status", "QC", "Words", "Refresh?", "To", "Subject"):
        t.add_column(col)
    for d in S().query(db.Draft).filter(db.Draft.status != "superseded").order_by(db.Draft.id):
        qc = d.qc or {}
        t.add_row(str(d.id), escape(d.company.name), d.kind, d.status + (f" @ {fmt(d.scheduled_at)} UTC" if d.status == "scheduled" else ""),
                  "pass" if qc.get("passed") else "reject" if qc else "—", str(qc.get("words", "")), "yes" if d.needs_refresh else "",
                  escape(f"{d.contact.email or '—'} [{d.contact.email_status}]") if d.contact else "—", escape(d.subject or ""))
    console.print(t)


@app.command()
def followups():
    """Prepare follow-ups that are due. Each needs its own approval before it can be sent."""
    created = sender.prepare_followups(S(), cfg)
    console.print(f"{len(created)} new follow-up draft(s) prepared.")
    waiting = S().query(db.Draft).filter(db.Draft.kind == "followup", db.Draft.status.in_(news.UNSENT)).all()
    for d in waiting:
        console.print(f"  #{d.id} {escape(d.company.name)} ({d.status}) — approve with: outreach approve {d.company_id}")
    if not waiting:
        console.print(f"No follow-ups waiting. They become due {cfg['followup_days']} days after an unanswered email.")


# ---------------------------------------------------------------- tracking

def _replies_table(rows, title):
    t = Table(title=title)
    for col in ("Received", "Company", "From", "Type", "Snippet"):
        t.add_column(col)
    for r in rows:
        c = S().get(db.Company, r.company_id)
        t.add_row(fmt(r.received_at), escape(c.name if c else "?"), escape(r.from_email or ""), r.classification, escape((r.snippet or "")[:90]))
    console.print(t)


@app.command()
def inbox():
    """Check managed outreach threads for new replies, bounces and opt-outs."""
    try:
        client = gmail.get_client(cfg)
    except gmail.GmailError as e:
        fail(str(e))
    new = sender.sync_replies(S(), client, cfg)
    _replies_table(new, f"New since last check ({len(new)})")
    console.print("Only threads started by this tool are read. Replied companies get no follow-ups; opt-outs and declines are suppressed.")


@app.command()
def replies():
    """Every reply recorded so far."""
    _replies_table(S().query(db.Reply).order_by(db.Reply.id.desc()).all(), "Replies")


@app.command()
def pipeline():
    """Where every company stands."""
    counts = Counter(c.state for c in S().query(db.Company))
    order = ["discovered", "researched", "shortlisted", "drafted", "reviewed", "approved", "scheduled", "sent", "followup_pending",
             "replied", "closed", "bounced", "rejected", "opted_out", "unavailable"]
    t = Table(title="Pipeline")
    t.add_column("State")
    t.add_column("Companies", justify="right")
    for s in order:
        if counts.get(s):
            t.add_row(s, str(counts[s]))
    console.print(t)
    active = [c for c in S().query(db.Company) if c.state not in ("discovered", "researched")]
    if active:
        console.print(company_table(active, "In motion"))


@app.command()
def stats():
    """Campaign numbers."""
    s = S()
    sent_initial = s.query(db.SentMessage).filter_by(kind="initial", status="sent").count()
    by_kind = Counter(r.classification for r in s.query(db.Reply))
    human = by_kind["reply"] + by_kind["negative"] + by_kind["opt_out"]
    rows = [("Companies tracked", s.query(db.Company).count()),
            ("Researched", s.query(db.Company).filter(db.Company.researched_at.isnot(None)).count()),
            ("Shortlisted", s.query(db.Company).filter_by(shortlisted=True).count()),
            ("News items stored", s.query(db.NewsItem).count()),
            ("Drafts awaiting approval", s.query(db.Draft).filter(db.Draft.status.in_(("drafted", "reviewed"))).count()),
            ("Approved, unsent", s.query(db.Draft).filter(db.Draft.status.in_(("approved", "scheduled"))).count()),
            ("Initial emails sent", sent_initial),
            ("Follow-ups sent", s.query(db.SentMessage).filter_by(kind="followup", status="sent").count()),
            ("Replies (human)", human), ("Positive/neutral replies", by_kind["reply"]), ("Declines", by_kind["negative"]),
            ("Opt-outs", by_kind["opt_out"]), ("Bounces", by_kind["bounce"]),
            ("Reply rate", f"{human / sent_initial:.0%}" if sent_initial else "—"),
            ("Suppressed addresses", s.query(db.Suppression).count())]
    t = Table(title="Campaign stats")
    t.add_column("Metric")
    t.add_column("Value", justify="right")
    for k, v in rows:
        t.add_row(k, str(v))
    console.print(t)
    leads = Counter(c.primary_project for c in s.query(db.Company).filter(db.Company.primary_project.isnot(None)))
    if leads:
        console.print("Lead project across companies: " + escape(", ".join(f"{k} ×{v}" for k, v in leads.most_common())))


EXPORT = ["id", "name", "domain", "website", "industry", "employee_count", "employee_source", "employee_verified_at", "location",
          "remote", "founded_year", "stability", "stability_confidence", "hiring", "score", "evidence_confidence", "state",
          "shortlisted", "primary_project", "researched_at", "news_checked_at", "risk_flags", "notes"]


@app.command()
def export(path: Path):
    """Export companies to .csv or .json."""
    rows = []
    for c in S().query(db.Company).order_by(db.Company.score.desc()):
        row = {f: getattr(c, f) for f in EXPORT}
        k = c.contacts[0] if c.contacts else None
        row.update(contact_name=k.name if k else None, contact_title=k.title if k else None, contact_email=k.email if k else None,
                   email_status=k.email_status if k else None, sources=c.sources)
        rows.append(row)
    if path.suffix.lower() == ".json":
        path.write_text(json.dumps(rows, indent=2, default=str))
    else:
        with path.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else EXPORT)
            w.writeheader()
            w.writerows({k: "; ".join(v) if isinstance(v, list) else v for k, v in r.items()} for r in rows)
    console.print(f"Exported {len(rows)} companies to {path}")


@app.command()
def pause():
    """KILL SWITCH: block every outgoing send immediately, including scheduled ones."""
    db.kv_set(S(), "paused", True)
    console.print("[red]All sending is paused.[/red] Nothing will be sent until `outreach resume`.")


@app.command()
def resume():
    """Release the kill switch."""
    db.kv_set(S(), "paused", False)
    console.print("Sending re-enabled (still subject to approval, caps and draft-only mode).")


@app.command()
def close(cid: int, unavailable: bool = typer.Option(False, help="Mark as unavailable instead of closed.")):
    """Stop pursuing a company."""
    c = company_or_fail(cid)
    db.transition(c, "unavailable" if unavailable else "closed")
    S().commit()


@app.command()
def suppress(email: str, reason: str = "manual"):
    """Never email this address again."""
    sender._suppress(S(), email, reason)
    S().commit()
    console.print(f"{escape(email)} suppressed.")
