"""Configurable ranking. `score` is attractiveness, not a probability; evidence confidence is reported separately."""
from datetime import timedelta

from . import db

STABILITY = {"Strong Evidence": 1.0, "Moderate Evidence": 0.75, "Uncertain": 0.45, "Elevated Risk": 0.15}
HIRING = {"intern_evidence": 1.0, "hiring_eng": 0.7, "unknown": 0.4, "none": 0.25}


def size_fit(n) -> float:
    if n is None:
        return 0.5
    if n < 15:
        return 0.3
    if n <= 50:
        return 0.8
    if n <= 300:
        return 1.0
    if n <= 750:
        return 0.6
    return 0.0


def qualify(company, cfg) -> tuple[bool, str]:
    n = company.employee_count
    if n is not None and n > cfg["size_max"]:
        return False, f"{n} employees is over the {cfg['size_max']} cap"
    if n is not None and n < cfg["size_min"]:
        return False, f"{n} employees is under the {cfg['size_min']} floor"
    return True, ""


def score(company, cfg, match_score=None, news=()) -> tuple[float, dict, float]:
    """Return (attractiveness 0-100, per-category breakdown 0-100, evidence confidence 0-1)."""
    contacts = company.contacts or []
    if any(c.email_status in ("verified_public", "user_verified") for c in contacts):
        contact = 1.0
    else:
        contact = 0.6 if contacts else 0.2
    fit = size_fit(company.employee_count)
    stability = STABILITY.get(company.stability, 0.45)
    if company.founded_year and db.now().year - company.founded_year >= 5:
        stability = min(1.0, stability + 0.1)
    if company.risk_flags:
        stability = min(stability, 0.15)
    timing, cutoff = 0.3, db.now() - timedelta(days=45)
    for n in news:
        if (n.published_at or n.fetched_at or db.now()) >= cutoff:
            timing = 0.0 if n.category == "layoffs" else max(timing, (n.significance or 0) / 100)
    if match_score is None:
        match_score = (company.breakdown or {}).get("tech_match", 50)
    parts = {
        "tech_match": match_score,
        "access": 100 * (0.6 * fit + 0.4 * contact),
        "stability": 100 * stability,
        "intern_potential": 100 * (0.7 * HIRING.get(company.hiring, 0.4) + 0.3 * fit),
        "timing": 100 * timing,
        "interest": company.interest if company.interest is not None else 50,
    }
    w = cfg["weights"]
    total = sum(parts[k] * w[k] for k in parts) / sum(w.values())
    fresh = bool(company.researched_at and company.researched_at >= db.now() - timedelta(days=30))
    evidence = [bool(company.employee_source), len(company.sources or []) >= 2, fresh,
                (company.stability_confidence or 0) >= 0.6, contact == 1.0]
    return round(total, 1), {k: round(v, 1) for k, v in parts.items()}, round(sum(evidence) / len(evidence), 2)


def apply(session, company, cfg, match_score=None) -> None:
    news = session.query(db.NewsItem).filter_by(company_id=company.id).all() if company.id else []
    company.score, company.breakdown, company.evidence_confidence = score(company, cfg, match_score, news)


def filter_companies(session, size=None, industry=None, stable=False, stack=None, hiring=None, location=None,
                     has_contact=False, fresh_days=None, min_score=None, shortlist=False, include_hidden=False):
    rows = session.query(db.Company).all()
    has = lambda needle, hay: needle.lower() in (hay or "").lower()
    out = []
    for c in rows:
        if (c.hidden or c.excluded_reason) and not include_hidden:
            continue
        if shortlist and not c.shortlisted:
            continue
        if size and not (c.employee_count is not None and size[0] <= c.employee_count <= size[1]):
            continue
        if industry and not (has(industry, c.industry) or industry.lower() in (c.tags or [])):
            continue
        if stable and c.stability not in ("Strong Evidence", "Moderate Evidence"):
            continue
        if stack and not any(has(stack, s) for s in c.stack or []):
            continue
        if hiring and c.hiring != hiring:
            continue
        if location and not (has(location, c.location) or (location.lower() == "remote" and c.remote)):
            continue
        if has_contact and not any(k.email_status in ("verified_public", "user_verified") for k in c.contacts):
            continue
        if fresh_days and not (c.researched_at and c.researched_at >= db.now() - timedelta(days=fresh_days)):
            continue
        if min_score is not None and (c.score or 0) < min_score:
            continue
        out.append(c)
    return sorted(out, key=lambda c: (c.user_rank is None, c.user_rank or 0, -(c.score or 0)))
