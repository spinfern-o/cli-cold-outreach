"""Agent E: persistent, deduplicated, source-backed company news memory and its scheduled tick."""
import hashlib
import logging
import math
import re
import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

from . import db, discovery, llm, scoring
from .schemas import NewsFinding, NewsResult, Opening

log = logging.getLogger("companyscout.news")
UNSENT = ("drafted", "reviewed", "approved", "scheduled")
KEYWORDS = [  # shortcut: keyword triage for feed entries only; LLM-found items arrive already categorised
    ("layoffs", 90, r"layoff|laid off|lays off|workforce reduction|downsiz"),
    ("acquisition", 80, r"acquir|acquisition|merger|merges with"),
    ("funding", 75, r"raises \$|series [a-e]\b|funding round|seed round"),
    ("hiring", 70, r"internship|we're hiring|we are hiring|join our team|new grad"),
    ("product", 60, r"launch|introduc|announc|releas|now available|new feature"),
    ("leadership", 55, r"appoints|joins as|new (cto|ceo|vp)|names .* (cto|ceo)"),
]


def classify(text: str) -> tuple[str, int]:
    for category, significance, pattern in KEYWORDS:
        if re.search(pattern, text.lower()):
            return category, significance
    return "other", 25


def _canon(url: str) -> str:
    u = urlparse(url)
    return f"{(u.hostname or '').lower().removeprefix('www.')}{u.path.rstrip('/')}"


def _norm(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


def _when(value):
    if not value:
        return None
    try:
        d = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            d = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
    return d.astimezone(timezone.utc).replace(tzinfo=None) if d.tzinfo else d


def parse_feed(xml_text: str) -> list[dict]:
    """RSS 2.0 and Atom entries as {title, url, summary, published (ISO)}."""
    root = ET.fromstring(xml_text)
    local = lambda el: el.tag.rsplit("}", 1)[-1]
    out = []
    for el in root.iter():
        if local(el) not in ("item", "entry"):
            continue
        f = {local(ch): ch for ch in el}
        link = f.get("link")
        url = (link.get("href") or (link.text or "").strip()) if link is not None else ""
        text = lambda k: (f[k].text or "").strip() if k in f else ""
        when = _when(text("pubDate") or text("updated") or text("published"))
        if text("title") and url:
            out.append({"title": text("title"), "url": url, "summary": re.sub(r"<[^>]+>", " ", text("description") or text("summary"))[:500],
                        "published": when.isoformat() if when else None})
    return out


def send_notification(title: str, message: str) -> None:
    # Text travels as argv, never inside the script source, so a hostile headline cannot inject AppleScript.
    script = 'on run argv\ndisplay notification (item 2 of argv) with title (item 1 of argv)\nend run'
    try:
        subprocess.run(["osascript", "-e", script, title, message], capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        pass


def ingest(session, company, findings, cfg, verified=False, provider=None, profile=None, notify=None, source="search"):
    """Store unseen findings; verified ones for a company also rescore it, flag risk and flag unsent drafts. Returns new rows."""
    notify = notify or (send_notification if cfg["notify"] else (lambda *a: None))
    fresh = []
    # No flush (and so no SQLite write lock) until every slow model call below is done; the CLI and the
    # launchd monitor share this database and a writer waiting past busy_timeout fails with "database is locked".
    with session.no_autoflush:
        cid = company.id if company is not None else None
        seen = {_norm(t) for (t,) in session.query(db.NewsItem.title).filter(
            db.NewsItem.company_id == cid if cid else db.NewsItem.company_id.is_(None))}
        for f in findings:
            key = hashlib.sha1(f"{cid}|{_canon(f.url)}".encode()).hexdigest()
            if _norm(f.title) in seen or session.query(db.NewsItem).filter_by(dedupe_key=key).first():
                continue
            seen.add(_norm(f.title))
            fresh.append((f, key))
        impact = company is not None and verified
        openings = [_opening(company, f, cfg, provider, profile) if impact else None for f, _ in fresh]
    stored = []
    for f, key in fresh:
        item = db.NewsItem(company_id=cid, dedupe_key=key, title=f.title, url=f.url, summary=f.summary, category=f.category,
                           significance=f.significance, published_at=_when(f.published), source=source, verified=verified)
        session.add(item)
        stored.append(item)
    session.flush()
    if impact and stored:
        for item, o in zip(stored, openings):
            _impact(session, company, item, cfg, profile, notify, o)
        scoring.apply(session, company, cfg)
    session.commit()
    return stored


def _opening(company, f, cfg, provider, profile):
    """The model's suggested opening for significant, non-risk news; None when not needed or the call fails."""
    if provider is None or profile is None or f.category == "layoffs" or (f.significance or 0) < cfg["news_significant"]:
        return None
    try:
        return llm.ask(provider, "opening", _opening_prompt(company, f, profile), Opening, retries=2, cache=False)
    except llm.ProviderError as e:
        log.warning("opening suggestion failed", extra={"data": {"error": str(e)[:200]}})
        return None


def _impact(session, company, item, cfg, profile, notify, o=None):
    risk = item.category == "layoffs"
    if risk:
        if company.stability != "Elevated Risk":
            session.add(db.History(company_id=company.id, field="stability", old=str(company.stability),
                                   new="Elevated Risk", source=item.url))
            company.stability = "Elevated Risk"
        company.risk_flags = (company.risk_flags or []) + [f"{item.title} ({item.url})"]
    elif (item.significance or 0) < cfg["news_significant"]:
        return
    suggestion = (f"RISK — reconsider before sending: {item.title} ({item.url})" if risk
                  else f"Recent news to reference: {item.title} ({item.url})")
    if o is not None:
        item.suggested_opening = o.suggested_opening
        suggestion += f"\nSuggested opening: {o.suggested_opening}\nWhy it matters: {o.relevance}"
        if o.project in {p.name for p in profile.projects} and o.project != company.primary_project:
            session.add(db.History(company_id=company.id, field="primary_project", old=str(company.primary_project),
                                   new=o.project, source=item.url))
            company.primary_project = o.project
    # Approved text is never rewritten here: the draft is only flagged and the suggestion stored beside it.
    for draft in session.query(db.Draft).filter(db.Draft.company_id == company.id, db.Draft.status.in_(UNSENT)):
        draft.needs_refresh, draft.refresh_suggestion = True, suggestion
    db.log_activity(session, "risk" if risk else "news", f"{company.name}: {item.title}", company.id)
    notify(f"CompanyScout: {company.name}", ("Risk signal: " if risk else "") + item.title)


def _opening_prompt(company, item, profile):
    projects = "\n".join(f"- {p.name}: {p.summary}" for p in profile.projects)
    return (f"TASK: opening\nCompany: {company.name} — {company.description}\nNews: {item.title}. {item.summary}\n"
            f"Candidate projects:\n{projects}\nDoes this news change internship hiring potential (affects_hiring)? "
            "Which ONE candidate project does it make most relevant (project, exact name) and why (relevance)? "
            "Write suggested_opening: one or two plain sentences that could open a cold email referencing this news. "
            "Use only the facts above.")


def due(session, cfg, now=None) -> list:
    """Companies whose news check is overdue: shortlist first, then the rest, oldest check first. Timestamp-based, so
    anything missed while the Mac was asleep is simply still due on the next tick."""
    now = now or db.now()
    out = []
    for c in session.query(db.Company).filter(db.Company.state != "discovered"):
        if c.hidden or c.excluded_reason or c.state in db.TERMINAL:
            continue
        hours = cfg["shortlist_interval_h"] if c.shortlisted else cfg["tracked_interval_h"]
        if c.news_checked_at is None or c.news_checked_at <= now - timedelta(hours=hours):
            out.append(c)
    return sorted(out, key=lambda c: (not c.shortlisted, c.news_checked_at or datetime.min))


def check_company(session, provider, cfg, company, profile=None, fetch=discovery.fetch_url, notify=None) -> int:
    new = 0
    kw = dict(provider=provider, profile=profile, notify=notify)
    if company.feed_url:
        try:
            recent = [e for e in parse_feed(fetch(company.feed_url))
                      if not e["published"] or _when(e["published"]) >= db.now() - timedelta(days=90)]
            found = [NewsFinding(title=e["title"], url=e["url"], summary=e["summary"], published=e["published"],
                                 **dict(zip(("category", "significance"), classify(e["title"] + " " + e["summary"]))))
                     for e in recent[:20]]
            new += len(ingest(session, company, found, cfg, verified=True, source="feed", **kw))
        except Exception as e:
            log.info("feed check failed", extra={"data": {"feed": company.feed_url, "error": str(e)[:200]}})
    # Web search costs a Claude call: always for the shortlist, otherwise only when there is no feed to read.
    if company.shortlisted or not company.feed_url:
        since = (company.news_checked_at or db.now() - timedelta(days=60)).date().isoformat()
        prompt = (f"TASK: news\nFind news about {company.name} ({company.website or company.domain}) published since {since}: "
                  "product launches, engineering hiring or internships, funding, acquisitions, leadership changes, layoffs "
                  "or other risk signals. Only items with a real, dated source URL. Return an empty list if there is none. "
                  "significance 0-100 = how much it should change an internship applicant's view of the company.")
        try:
            items = llm.ask(provider, "news", prompt, NewsResult, web=True, retries=2, cache=False).items
            confirmed = [f for f in items if _mentions(f.url, company.name, fetch)]
            new += len(ingest(session, company, confirmed, cfg, verified=True, **kw))
            new += len(ingest(session, company, [f for f in items if f not in confirmed], cfg, verified=False, **kw))
        except llm.ProviderError as e:
            log.warning("news search failed", extra={"data": {"company": company.name, "error": str(e)[:200]}})
    company.news_checked_at = db.now()
    session.commit()
    return new


def _mentions(url, name, fetch) -> bool:
    """Verification = we fetched the cited page ourselves and it names the company."""
    try:
        return name.lower() in fetch(url).lower()
    except Exception:
        return False


def tick(session, provider, cfg, now=None, fetch=discovery.fetch_url, notify=None, profile=None):
    """One scheduled pass. Safe to run at any time; records how many scheduled ticks were missed since the last one."""
    now = now or db.now()
    last = session.query(db.MonitorRun).order_by(db.MonitorRun.id.desc()).first()
    missed = max(0, int((now - last.started_at).total_seconds() // cfg["monitor_tick_s"]) - 1) if last else 0
    run = db.MonitorRun(started_at=now, missed_ticks=missed, checked=0, new_items=0)
    session.add(run)
    session.commit()
    stale = lambda key, hours: (db.kv_get(session, key) or "") < (now - timedelta(hours=hours)).isoformat()
    try:
        for company in due(session, cfg, now)[:cfg["monitor_max_per_tick"]]:
            try:
                run.new_items += check_company(session, provider, cfg, company, profile, fetch, notify)
            except Exception as e:
                session.rollback()
                log.exception("company check failed", extra={"data": {"error": str(e)[:200]}})
            run.checked += 1
            session.commit()  # else the next lazy load flushes this and holds the write lock through network calls
        if stale("last_market_check", cfg["tracked_interval_h"]):
            sectors = sorted({c.industry for c in session.query(db.Company).filter_by(shortlisted=True) if c.industry})
            prompt = ("TASK: news\nGeneral market developments from the last two weeks relevant to a CS student seeking a "
                      "Summer 2027 software/AI internship at small and midsize US software companies: early-career hiring "
                      f"trends and notable developments in these sectors: {', '.join(sectors) or 'software'}. Dated sources only.")
            items = llm.ask(provider, "news", prompt, NewsResult, web=True, retries=2, cache=False).items
            run.new_items += len(ingest(session, None, items, cfg, source="market", notify=notify))
            db.kv_set(session, "last_market_check", now.isoformat())
        if cfg["weekly_discovery_limit"] and stale("last_discovery", cfg["discovery_interval_h"]):
            added = discovery.discover(session, provider, cfg, cfg["weekly_discovery_limit"])
            fresh = session.query(db.Company).filter_by(state="discovered", excluded_reason=None).all()
            discovery.research_all(session, provider, cfg, fresh[:cfg["weekly_discovery_limit"]], profile, fetch)
            db.log_activity(session, "discovery", f"Weekly discovery added {added} companies")
    except Exception as e:
        session.rollback()
        run.error = str(e)[:500]
        log.exception("monitor tick failed")
    run.finished_at = db.now()
    session.commit()
    return run


def search(session, query: str, limit: int = 20) -> list[dict]:
    """Ranked keyword search over stored news and company profiles.
    shortcut: scans rows in Python, fine for thousands of rows; move to SQLite FTS5 if the memory grows past that."""
    words = re.findall(r"\w+", query.lower())
    need = max(1, math.ceil(len(words) / 2))
    hits = []

    def consider(kind, title, text, url, when, company):
        score = sum(1 for w in words if re.search(rf"\b{re.escape(w)}", text.lower()))
        if score >= need:
            hits.append({"kind": kind, "title": title, "url": url, "date": when, "company": company, "score": score})

    for n in session.query(db.NewsItem):
        name = n.company.name if n.company else "market"
        consider("news", n.title, f"{n.title} {n.summary} {n.category} {name}", n.url, n.published_at or n.fetched_at, name)
    for c in session.query(db.Company).filter(db.Company.description.isnot(None)):
        text = f"{c.name} {c.description} {c.industry} {' '.join(c.tags or [])} {' '.join(c.stack or [])}"
        consider("company", c.name, text, c.website, c.researched_at, c.name)
    return sorted(hits, key=lambda h: (-h["score"], -(h["date"] or datetime.min).timestamp()))[:limit]
