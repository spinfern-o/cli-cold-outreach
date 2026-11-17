"""Agent A (discovery) and Agent B (company intelligence)."""
import logging
import ssl
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import certifi

from . import db, llm, profile as profile_mod, scoring
from .schemas import TAGS, CompanyResearch, DiscoveryResult

log = logging.getLogger("companyscout.discovery")

# Deliberately broad so discovery does not keep resurfacing the same well-known AI startups.
SEGMENTS = [
    "bootstrapped or profitable B2B SaaS companies", "vertical software vendors for a specific industry",
    "developer tooling and infrastructure providers", "financial technology and accounting software companies",
    "applied AI and machine learning product companies", "robotics, industrial automation and computer vision firms",
    "healthcare technology and health IT companies", "specialized software consultancies and engineering firms",
    "enterprise software vendors selling to mid-market customers", "manufacturing, logistics and supply-chain software companies",
    "education technology and public-sector software companies", "data, analytics and document-processing software companies",
    "e-commerce platforms, tooling and agencies with engineering teams", "insurance, legal and real-estate software companies",
    "software companies headquartered in Michigan or the Midwest", "traditional businesses with a meaningful in-house software team",
]
TRACKED = ["employee_count", "stability", "hiring", "industry", "location"]


def canonical_domain(url: str) -> str:
    host = urlparse(url if "//" in url else "//" + url).hostname or ""
    return host.lower().removeprefix("www.")


# python.org builds of Python ship without system root certificates; without this every HTTPS fetch fails.
SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())


def fetch_url(url: str, limit: int = 2_000_000) -> str:
    """Plain, identified HTTP GET. http(s) only; nothing fetched is ever executed."""
    if urlparse(url).scheme not in ("http", "https"):
        raise ValueError(f"refusing non-http URL: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "CompanyScout/0.1 (personal internship research)"})
    with urllib.request.urlopen(req, timeout=15, context=SSL_CONTEXT) as resp:
        return resp.read(limit).decode("utf-8", errors="replace")


def verify_email(email, source_url, fetch=fetch_url) -> str:
    """An address counts as verified only if it is literally printed on the page cited for it."""
    if not email:
        return "unknown"
    if not source_url:
        return "inferred"
    try:
        return "verified_public" if email.lower() in fetch(source_url).lower() else "inferred"
    except Exception as e:  # unreachable page = unverified, never an error for the caller
        log.info("email source fetch failed", extra={"data": {"url": source_url, "error": str(e)[:200]}})
        return "inferred"


def _discover_prompt(segment, cfg, known, n=10):
    return (f"TASK: discover\nFind {n} real companies in this segment: {segment}.\n"
            f"Requirements: {cfg['size_min']}-{cfg['size_max']} employees (51-300 is ideal); a real product with paying "
            "customers or a credible operating history; has software engineers on staff; small enough that a CTO, founder "
            "or engineering manager could plausibly read a cold email. Established, stable private companies are as "
            "valuable as startups. Venture funding and public job postings are NOT required.\n"
            f"Location preferences: {', '.join(cfg['locations'])}. US companies only.\n"
            "Use web search (company sites, careers pages, business directories, trade publications). For each company give "
            "the official website, an employee estimate with the URL it came from (null if unknown), and source_url: the "
            "page where you confirmed the company exists.\n"
            f"Do not return any of these already-known domains: {', '.join(sorted(known)[-200:]) or 'none'}.")


def discover(session, provider, cfg, limit, segments=None, progress=None) -> int:
    segments = list(segments or SEGMENTS)
    cursor = db.kv_get(session, "discovery_cursor", 0) % len(segments)
    segments = segments[cursor:] + segments[:cursor]
    known = {d for (d,) in session.query(db.Company.domain)}
    added, failed, calls = 0, [], [0]

    def call(segment):
        calls[0] += 1
        try:
            return llm.ask(provider, "discover", _discover_prompt(segment, cfg, known), DiscoveryResult, web=True).companies
        except llm.ProviderError as e:
            log.warning("discovery call failed", extra={"data": {"segment": segment, "error": str(e)[:200]}})
            failed.append(segment)
            return []

    width = max(1, cfg["research_parallel"])
    for _ in range(3):  # extra passes only while they keep producing new companies
        before = added
        for i in range(0, len(segments), width):
            if added >= limit:
                break
            with ThreadPoolExecutor(width) as pool:
                batches = list(pool.map(call, segments[i:i + width]))
            for found in (c for batch in batches for c in batch):
                domain = canonical_domain(found.website)
                if not domain or domain in known or added >= limit:
                    continue
                known.add(domain)
                company = db.Company(name=found.name, domain=domain, website=found.website, industry=found.industry,
                                     description=found.description, employee_count=found.employee_estimate,
                                     employee_source=found.employee_source, location=found.location,
                                     sources=[found.source_url], state="discovered")
                company.excluded_reason = scoring.qualify(company, cfg)[1] or None
                session.add(company)
                added += 1
            session.commit()
            if progress:
                progress(added)
        if added >= limit or added == before:
            break
    db.kv_set(session, "discovery_cursor", cursor + 1)
    if len(failed) < calls[0]:  # every call failed (e.g. a usage limit): leave it due so the next tick retries
        db.kv_set(session, "last_discovery", db.now().isoformat())
    return added


def _research_prompt(name, website, industry, description):
    return (f"TASK: research\nResearch this company for an internship outreach decision: {name} ({website}).\n"
            f"Known so far: industry={industry or 'unknown'}; {description or ''}\n"
            "Use its website, careers page, engineering blog, GitHub, and reputable news. Report:\n"
            "- product (2-3 specific sentences) and named customers\n"
            "- employee_count with employee_source URL; founded_year; location; remote\n"
            "- stability: one of Strong Evidence / Moderate Evidence / Uncertain / Elevated Risk, with stability_confidence "
            "0-1 and the evidence (years operating, customers, disclosed revenue/profitability, hiring, funding, layoffs). "
            "Funding alone is not stability. Never invent financial figures.\n"
            "- stack: technologies the company documents using\n"
            f"- tags: only from this list: {', '.join(TAGS)}\n"
            "- engineering_openings (current titles); has_internship_evidence: true ONLY if you found concrete evidence of "
            "intern, co-op, new-grad or early-career engineering hiring (past or present), with internship_evidence "
            "describing it; otherwise false; "
            "engineering_presence (blog, GitHub, talks)\n"
            "- recent_announcements (last 12 months, dated), risk_signals (layoffs, shutdown, lawsuits)\n"
            "- feed_url: the company's blog/news RSS or Atom feed if one exists\n"
            "- contact: the most reachable technical decision-maker (CTO, VP Eng, engineering manager, technical founder). "
            "Include email ONLY if that exact address is printed on a public professional page, and give that page as "
            "email_source_url. If their own address is not printed anywhere, use instead a company address printed on the "
            "company's own website that a hiring or engineering inquiry could reach (prefer careers@/jobs@/engineering@, "
            "then hello@/info@/contact@; never sales-only, support-ticket or press addresses), with that page as "
            "email_source_url; keep the decision-maker's name and title. Do not guess or pattern-match addresses; if "
            "neither exists, email is null.\n"
            "- sources: every URL you relied on.")


def apply_research(session, company, r: CompanyResearch, cfg, profile, fetch=fetch_url) -> None:
    hiring = "intern_evidence" if r.has_internship_evidence else "hiring_eng" if r.engineering_openings else "unknown"
    new = {"employee_count": r.employee_count or company.employee_count, "stability": r.stability, "hiring": hiring,
           "industry": r.industry or company.industry, "location": r.location or company.location}
    for field in TRACKED:
        old = getattr(company, field)
        if old != new[field]:
            if old is not None:
                session.add(db.History(company_id=company.id, field=field, old=str(old), new=str(new[field]), source="research"))
            setattr(company, field, new[field])
    if r.employee_count and r.employee_source:
        company.employee_source, company.employee_verified_at = r.employee_source, db.now()
    company.founded_year, company.remote = r.founded_year or company.founded_year, r.remote
    company.stability_confidence, company.stack, company.tags = r.stability_confidence, r.stack, r.tags
    company.description, company.research, company.feed_url = r.product, r.model_dump(), r.feed_url or company.feed_url
    company.sources = sorted(set((company.sources or []) + r.sources))
    if r.stability == "Elevated Risk":
        company.risk_flags = sorted(set((company.risk_flags or []) + r.risk_signals))
    if r.contact and (r.contact.name or r.contact.email):
        if not company.contacts:
            company.contacts.append(db.Contact())  # append (not backref) so the session cascades the insert
        contact = company.contacts[0]
        if not (contact.email_status == "user_verified" and contact.email == r.contact.email):
            if contact.email and contact.email != r.contact.email:
                session.add(db.History(company_id=company.id, field="contact_email", old=contact.email,
                                       new=str(r.contact.email), source="research"))
            contact.email = r.contact.email
            contact.email_source_url = r.contact.email_source_url
            contact.email_status = verify_email(r.contact.email, r.contact.email_source_url, fetch)
            contact.verified_at = db.now() if contact.email_status == "verified_public" else None
        contact.name, contact.title, contact.profile_url = r.contact.name, r.contact.title, r.contact.profile_url
    if not (company.excluded_reason or "").startswith("user:"):
        company.excluded_reason = scoring.qualify(company, cfg)[1] or None
    match_score = None
    if profile:
        usage = Counter(p for (p,) in session.query(db.Company.primary_project)
                        .filter(db.Company.id != company.id, db.Company.primary_project.isnot(None)))
        company.primary_project, match_score, company.match_rationale = profile_mod.match(profile, company, usage)
    company.researched_at = db.now()
    scoring.apply(session, company, cfg, match_score)
    if (company.state or "discovered") == "discovered":
        db.transition(company, "researched")
    session.commit()


def research_all(session, provider, cfg, companies, profile=None, fetch=fetch_url, backoff=2.0, progress=None):
    """One research task per company, in parallel batches. A failure leaves that company unchanged. Returns (done, failed)."""
    prompts = {c.id: _research_prompt(c.name, c.website or c.domain, c.industry, c.description) for c in companies}

    def call(cid):
        try:
            return llm.ask(provider, "research", prompts[cid], CompanyResearch, web=True, backoff=backoff)
        except llm.ProviderError as e:
            log.warning("research failed", extra={"data": {"company_id": cid, "error": str(e)[:200]}})
            return None

    done = failed = 0
    with ThreadPoolExecutor(max(1, cfg["research_parallel"])) as pool:
        for company, result in zip(companies, pool.map(call, [c.id for c in companies])):
            if result is None:
                failed += 1
            else:
                apply_research(session, company, result, cfg, profile, fetch)
                done += 1
            if progress:
                progress(company, result is not None)
    return done, failed
