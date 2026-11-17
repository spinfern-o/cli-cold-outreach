import pytest

from companyscout import db, discovery, llm
from companyscout.schemas import CompanyResearch


def research(**kw):
    base = dict(product="Workflow software for regional credit unions.", industry="fintech", employee_count=100,
                employee_source="https://acme.com/about", stability="Moderate Evidence", stability_confidence=0.7,
                stack=["Python"], tags=["fintech"], sources=["https://acme.com/about"])
    base.update(kw)
    return base


def found(name, website, n=100):
    return dict(name=name, website=website, industry="fintech", employee_estimate=n, source_url="https://directory.example/list")


def test_research_without_sources_is_rejected(session, cfg):
    provider = llm.MockProvider(lambda task, prompt: research(sources=[]))
    with pytest.raises(llm.ProviderError):
        llm.ask(provider, "research", "p", CompanyResearch, backoff=0)


def test_ask_recovers_after_two_provider_failures():
    calls = []

    def flaky(task, prompt):
        calls.append(1)
        if len(calls) < 3:
            raise llm.ProviderError("timeout")
        return research()

    assert llm.ask(llm.MockProvider(flaky), "research", "p", CompanyResearch, backoff=0).employee_count == 100
    assert len(calls) == 3


def test_ask_serves_repeat_prompt_from_cache(session, cfg):
    calls = []
    provider = llm.MockProvider(lambda task, prompt: calls.append(1) or research())
    for _ in range(2):
        llm.ask(provider, "research", "same prompt", CompanyResearch, session=session, cfg=cfg)
    assert len(calls) == 1


def test_discover_dedupes_by_canonical_domain(session, cfg):
    session.add(db.Company(name="Old", domain="old.com"))
    session.commit()
    batch = {"companies": [found("Acme", "https://www.acme.com/about"), found("Acme Inc", "http://acme.com"),
                           found("Old Co", "https://old.com")]}
    added = discovery.discover(session, llm.MockProvider(lambda t, p: batch), cfg, limit=5, segments=["fintech"])
    assert added == 1
    assert sorted(c.domain for c in session.query(db.Company)) == ["acme.com", "old.com"]


def test_discover_marks_oversize_company_excluded(session, cfg):
    batch = {"companies": [found("Huge", "https://huge.com", n=5000)]}
    discovery.discover(session, llm.MockProvider(lambda t, p: batch), cfg, limit=5, segments=["fintech"])
    assert "over" in session.query(db.Company).one().excluded_reason


def test_email_is_verified_only_when_printed_on_cited_page():
    page = lambda url: "<p>Contact our CTO: jane@acme.com</p>"
    assert discovery.verify_email("jane@acme.com", "https://acme.com/team", page) == "verified_public"
    assert discovery.verify_email("john@acme.com", "https://acme.com/team", page) == "inferred"
    assert discovery.verify_email("jane@acme.com", None, page) == "inferred"
    assert discovery.verify_email(None, None, page) == "unknown"


def test_research_change_is_recorded_in_history(session, cfg):
    c = db.Company(name="Acme", domain="acme.com")
    session.add(c)
    session.commit()
    no_fetch = lambda url: ""
    discovery.apply_research(session, c, CompanyResearch(**research()), cfg, None, no_fetch)
    discovery.apply_research(session, c, CompanyResearch(**research(employee_count=140)), cfg, None, no_fetch)
    change = session.query(db.History).filter_by(field="employee_count").order_by(db.History.id.desc()).first()
    assert (change.old, change.new) == ("100", "140")
    assert c.state == "researched" and c.score is not None and c.employee_verified_at is not None


def test_failed_research_leaves_company_untouched_and_others_proceed(session, cfg):
    session.add_all([db.Company(name="Good", domain="good.com"), db.Company(name="Bad", domain="bad.com")])
    session.commit()

    def handler(task, prompt):
        if "bad.com" in prompt:
            raise llm.ProviderError("rate limited")
        return research()

    done, failed = discovery.research_all(session, llm.MockProvider(handler), cfg, session.query(db.Company).all(),
                                          fetch=lambda url: "", backoff=0)
    assert (done, failed) == (1, 1)
    states = {c.name: c.state for c in session.query(db.Company)}
    assert states == {"Good": "researched", "Bad": "discovered"}


def test_text_saying_no_internships_is_not_internship_evidence(session, cfg):
    c = db.Company(name="Acme", domain="acme.com")
    session.add(c)
    session.commit()
    r = research(internship_evidence="None found. The careers page lists no internships.", engineering_openings=["Senior Engineer"])
    discovery.apply_research(session, c, CompanyResearch(**r), cfg, None, lambda url: "")
    assert c.hiring == "hiring_eng"
    r = research(has_internship_evidence=True, internship_evidence="Ran a 2023 software intern program.")
    discovery.apply_research(session, c, CompanyResearch(**r), cfg, None, lambda url: "")
    assert c.hiring == "intern_evidence"
