from datetime import timedelta

from companyscout import db, llm, news
from companyscout.schemas import NewsFinding

QUIET = lambda *a: None


def company(session, **kw):
    base = dict(name="Acme", domain="acme.com", state="researched", stability="Moderate Evidence", employee_count=95,
                tags=["fintech"], stack=[], risk_flags=[], sources=[])
    base.update(kw)
    c = db.Company(**base)
    session.add(c)
    session.commit()
    return c


def finding(**kw):
    base = dict(title="Acme launches AI document processing", url="https://acme.com/blog/ai-docs",
                summary="New feature reads invoices.", category="product", significance=80, published=db.now().date().isoformat())
    base.update(kw)
    return NewsFinding(**base)


def test_same_story_is_stored_once(session, cfg):
    c = company(session)
    news.ingest(session, c, [finding()], cfg, verified=True, notify=QUIET)
    again = [finding(url="https://acme.com/blog/ai-docs/?utm_source=x"), finding(url="https://other.com/repost")]
    assert news.ingest(session, c, again, cfg, verified=True, notify=QUIET) == []
    assert session.query(db.NewsItem).count() == 1


def test_due_honours_shortlist_and_tracked_intervals(session, cfg):
    t = db.now()
    company(session, name="ShortDue", domain="a.com", shortlisted=True, news_checked_at=t - timedelta(hours=13))
    company(session, name="ShortFresh", domain="b.com", shortlisted=True, news_checked_at=t - timedelta(hours=2))
    company(session, name="TrackedFresh", domain="c.com", news_checked_at=t - timedelta(hours=13))
    company(session, name="TrackedDue", domain="d.com", news_checked_at=t - timedelta(hours=49))
    company(session, name="Never", domain="e.com")
    company(session, name="Hidden", domain="f.com", hidden=True)
    assert [c.name for c in news.due(session, cfg, t)] == ["ShortDue", "Never", "TrackedDue"]


def test_everything_overdue_is_caught_up_after_a_long_gap(session, cfg):
    t = db.now()
    company(session, name="S", domain="a.com", shortlisted=True, news_checked_at=t)
    company(session, name="T", domain="b.com", news_checked_at=t)
    assert news.due(session, cfg, t) == []
    assert [c.name for c in news.due(session, cfg, t + timedelta(days=5))] == ["S", "T"]


def test_verified_layoffs_flag_elevated_risk(session, cfg):
    c = company(session)
    layoffs = finding(title="Acme lays off 30% of staff", url="https://news.example/acme-layoffs", category="layoffs", significance=90)
    news.ingest(session, c, [layoffs], cfg, verified=True, notify=QUIET)
    assert c.stability == "Elevated Risk"
    assert any("lays off" in f for f in c.risk_flags)
    assert session.query(db.History).filter_by(field="stability", new="Elevated Risk").count() == 1
    assert session.query(db.Activity).filter_by(kind="risk").count() == 1


def test_significant_news_flags_unsent_drafts_without_touching_approved_text(session, cfg):
    c = company(session)
    approved = db.Draft(company_id=c.id, body="approved body", status="approved", approved_hash="h1")
    sent = db.Draft(company_id=c.id, body="sent body", status="sent")
    session.add_all([approved, sent])
    session.commit()
    news.ingest(session, c, [finding()], cfg, verified=True, notify=QUIET)
    assert approved.needs_refresh is True and "AI document processing" in approved.refresh_suggestion
    assert (approved.body, approved.status, approved.approved_hash) == ("approved body", "approved", "h1")
    assert not sent.needs_refresh


def test_unverified_news_is_saved_but_triggers_nothing(session, cfg):
    c = company(session)
    session.add(db.Draft(company_id=c.id, body="b", status="drafted"))
    session.commit()
    layoffs = finding(title="Rumour: Acme layoffs", url="https://rumour.example/x", category="layoffs", significance=90)
    stored = news.ingest(session, c, [layoffs], cfg, verified=False, notify=QUIET)
    assert stored[0].verified is False and c.stability == "Moderate Evidence"
    assert not session.query(db.Draft).one().needs_refresh


def test_parse_feed_reads_rss_and_atom():
    rss = "<rss><channel><item><title>Hello</title><link>https://a.com/1</link><pubDate>Mon, 02 Nov 2026 10:00:00 GMT</pubDate></item></channel></rss>"
    atom = ('<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Hi</title><link href="https://a.com/2"/>'
            "<updated>2026-11-03T10:00:00Z</updated></entry></feed>")
    assert [(e["title"], e["url"], e["published"][:10]) for e in news.parse_feed(rss)] == [("Hello", "https://a.com/1", "2026-11-02")]
    assert [(e["title"], e["url"], e["published"][:10]) for e in news.parse_feed(atom)] == [("Hi", "https://a.com/2", "2026-11-03")]


def test_memory_search_finds_matching_news_first(session, cfg):
    c = company(session)
    news.ingest(session, c, [finding(), finding(title="Acme hires new CFO", url="https://acme.com/cfo", category="leadership", significance=40)],
                cfg, verified=True, notify=QUIET)
    hits = news.search(session, "AI product launch")
    assert hits[0]["title"] == "Acme launches AI document processing"


def test_tick_checks_due_companies_and_records_missed_ticks(session, cfg):
    c = company(session, shortlisted=True)
    session.add(db.MonitorRun(started_at=db.now() - timedelta(hours=5), finished_at=db.now() - timedelta(hours=5)))
    session.commit()
    db.kv_set(session, "last_discovery", db.now().isoformat())
    db.kv_set(session, "last_market_check", db.now().isoformat())
    provider = llm.MockProvider(lambda task, prompt: {"items": [finding().model_dump()]})
    run = news.tick(session, provider, cfg, fetch=lambda url: "Acme launches AI document processing", notify=QUIET)
    assert (run.checked, run.new_items, run.missed_ticks) == (1, 1, 4)
    assert c.news_checked_at is not None and session.query(db.NewsItem).one().verified is True
