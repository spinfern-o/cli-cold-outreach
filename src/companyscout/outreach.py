"""Agent C (email strategy), draft generation, Agent F (quality control) and approval rules."""
import hashlib
import json
import re
from datetime import timedelta
from pathlib import Path
from difflib import SequenceMatcher

from . import db, llm, style
from .schemas import EmailDraft, QCReview

VERIFIED = ("verified_public", "user_verified")
ORDER = ["discovered", "researched", "shortlisted", "drafted", "reviewed", "approved"]
SUFFIXES = {"robotics", "software", "technologies", "technology", "systems", "labs", "interfaces", "innovation",
            "data", "ai", "solutions", "group", "inc", "inc.", "corp", "corporation", "co", "co."}


def name_variants(name: str) -> set[str]:
    """How an email can fairly name a company: "Kit (ConvertKit)" -> Kit / ConvertKit, "Plus One Robotics" -> Plus One."""
    out = {name.strip()}
    m = re.match(r"\s*(.+?)\s*\((.+)\)\s*$", name)
    if m:
        out |= {m.group(1), m.group(2)}
    for n in list(out):
        words = n.split()
        if len(words) > 1 and words[-1].lower() in SUFFIXES:
            out.add(" ".join(words[:-1]))
    return out


# Agent C: the outreach strategy every draft and review is held to.
STRATEGY = """Cold-email strategy for technical internship outreach to small and midsize companies:
- Write to one engineer, not a hiring portal. Open with a specific, checkable observation about their product,
  engineering work or a recent announcement. No compliments, no flattery, no corporate language.
- Make exactly one technical connection: the single project from the candidate's background that is closest to
  what this company builds, described by what was built and how it was verified. Do not list the resume.
- Say concretely what the candidate could help with on their product (testing, tooling, evaluation, a feature area).
- Mention the 8VC Fellowship finalist credential only if the recipient is a founder/executive or the company is
  venture-backed; leave it out for established or bootstrapped firms where it means little.
- End with one low-pressure question about Summer 2027 engineering internships. One ask only.
- {lo}-{hi} words, plain text, no bullet lists, no links except at most one to the project, first-name greeting,
  sign off as "Larry Wang".
- Subject lines: under 60 characters (ideally 3-6 words), specific to the company or the shared technical area,
  never clickbait, never "Internship application" or "Opportunity".
- Shared inbox (careers@, hello@, info@ ...): someone triages it, so make it forwardable. The first two sentences say
  who the candidate is and what he wants; keep the named person's first name in the greeting if known; the one ask
  is "who would be the right person to ask about a Summer 2027 engineering internship?"
- Sound like one specific student, not an AI. Never use "it's not X, it's Y" / "not just X but Y" contrasts, tidy
  one-line closers, rhetorical questions, triads, em dashes, or stock phrases (I hope this finds you well, I'm
  reaching out, I came across, passionate about, excited to, would love the opportunity, leverage, quick chat).
  Avoid AI vocabulary (delve, crucial, pivotal, robust, seamless, showcase, foster, enhance, impressive, innovative).
  Mix short and long sentences, use plain verbs (built, checked, broke), and include one concrete detail only the
  candidate would know (how something was tested or what failed). Never invent a detail to sound human.
- Follow-up: one message, sent in the same thread about a week later, under 70 words, adds one new concrete detail
  rather than repeating the first email, and makes it easy to say no.
- Use only facts given below. Never state a number, technology, employer or result that is not in CANDIDATE FACTS,
  and never state anything about the company that is not in COMPANY RESEARCH."""


class ApprovalError(ValueError):
    pass


def content_hash(subject, body, to_email, attachment=None) -> str:
    """What an approval covers. With an attachment, its bytes too, so a changed or missing file blocks the send."""
    extra = "" if not attachment else "\n" + hashlib.sha256(Path(attachment).expanduser().read_bytes()).hexdigest()
    return hashlib.sha256(f"{to_email}\n{subject}\n{body}{extra}".encode()).hexdigest()


def _advance(company, target) -> None:
    state = company.state or "discovered"
    if state in ORDER and ORDER.index(state) < ORDER.index(target):
        for step in ORDER[ORDER.index(state) + 1:ORDER.index(target) + 1]:
            db.transition(company, step)


def _news(session, company):
    cutoff = db.now() - timedelta(days=60)
    rows = session.query(db.NewsItem).filter_by(company_id=company.id, verified=True).order_by(db.NewsItem.significance.desc())
    return [n for n in rows if (n.published_at or n.fetched_at) >= cutoff and n.category != "layoffs"][:3]


def _facts(profile, cfg, project_name=None):
    lines = [f"{profile.name} — {profile.headline}. {profile.education}",
             "Credentials: " + "; ".join(dict.fromkeys(list(profile.credentials) + list(cfg["credentials"])))]
    for p in profile.projects:
        lead = " (PRIMARY EXAMPLE FOR THIS EMAIL)" if p.name == project_name else ""
        lines.append(f"- {p.name}{lead}: {p.summary} Tech: {', '.join(p.technologies)}. Evidence: {' '.join(p.evidence)}")
    return "\n".join(lines)


def _research(session, company):
    r = dict(company.research or {})
    r.pop("contact", None)
    news = "\n".join(f"- {n.title} ({n.url}){' — ' + n.suggested_opening if n.suggested_opening else ''}" for n in _news(session, company))
    return f"{company.name} ({company.website or company.domain}), ~{company.employee_count} employees\n{json.dumps(r)}\nRecent verified news:\n{news or 'none'}"


def generate(session, provider, cfg, company, profile, force=False):
    """Draft one personalised email. An approved draft is never replaced unless the user explicitly forces it."""
    current = session.query(db.Draft).filter(db.Draft.company_id == company.id, db.Draft.kind == "initial",
                                             db.Draft.status.in_(("drafted", "reviewed", "approved", "scheduled"))).all()
    if any(d.status in ("approved", "scheduled") for d in current) and not force:
        raise ApprovalError(f"{company.name} already has an approved draft; regenerate with --force to replace it")
    contact = company.contacts[0] if company.contacts else None
    lo, hi = cfg["word_range"]
    prompt = (f"TASK: draft\nCompany: {company.name}\n{STRATEGY.format(lo=lo, hi=hi)}\n\n"
              f"RECIPIENT: {contact.name + ', ' + (contact.title or '') if contact else 'unknown — address the engineering team'}"
              f"{' (sent to the shared inbox ' + contact.email + ')' if contact and style.shared_inbox(contact.email) else ''}\n\n"
              f"COMPANY RESEARCH:\n{_research(session, company)}\n\nCANDIDATE FACTS:\n{_facts(profile, cfg, company.primary_project)}\n\n"
              "Return: subjects (exactly 3 options), body (the email), rationale (why this company and this project, "
              "citing the research used), followup (the one follow-up message).")
    out = llm.ask(provider, "draft", prompt, EmailDraft, session=session, cfg=cfg, cache=not force)
    for old in current:
        old.status = "superseded"
    draft = db.Draft(company_id=company.id, contact_id=contact.id if contact else None, subjects=out.subjects,
                     subject=out.subjects[0], body=out.body.strip(), rationale=out.rationale, followup_body=out.followup.strip())
    session.add(draft)
    if company.state in ("approved", "scheduled"):
        db.transition(company, "drafted")
    session.commit()
    qc(session, provider, cfg, draft, profile)
    return draft


def qc(session, provider, cfg, draft, profile) -> dict:
    """Independent review. Deterministic checks always run; the LLM reviewer adds judgement when a provider is given."""
    company, body = draft.company, draft.body or ""
    lo, hi = cfg["word_range"]
    words = len(body.split())
    issues, warnings, scores = [], [], {}
    if not lo <= words <= hi:
        issues.append(f"{words} words; target is {lo}-{hi}")
    if "2027" not in body or "?" not in body:
        issues.append("no clear question about Summer 2027 opportunities")
    if not any(re.search(rf"\b{re.escape(n)}\b", body, re.I) for n in name_variants(company.name)):
        issues.append("never names the company")
    style_issues, style_warnings = style.check(draft.subject or "", body)
    issues += style_issues
    warnings += style_warnings
    news = session.query(db.NewsItem).filter_by(company_id=company.id).all()
    allowed = " ".join([profile.source_text, _facts(profile, cfg), json.dumps(company.research or {}), company.description or "",
                        " ".join(f"{n.title} {n.summary}" for n in news)])
    invented = sorted({n for n in re.findall(r"\d+(?:[.,]\d+)*", body) if n != "2027" and n not in allowed})
    if invented:
        issues.append(f"numbers not found in resume or research: {', '.join(invented)}")
    others = session.query(db.Draft).filter(db.Draft.company_id != company.id, db.Draft.status.notin_(("superseded", "rejected")))
    for other in others:
        ratio = SequenceMatcher(None, body, other.body or "").ratio()
        if ratio > 0.8:
            issues.append(f"{ratio:.0%} similar to the draft for {other.company.name}")
            break
    contact = draft.contact
    if contact is None or not contact.name:
        warnings.append("no named recipient")
    if contact is None or contact.email_status not in VERIFIED:
        warnings.append("no verified email address; cannot be approved until one is verified")
    if provider is not None:
        prompt = (f"TASK: qc\nCompany: {company.name}\nYou are an independent reviewer of a cold internship email. Score 1-5: "
                  "personalization, technical_accuracy, resume_consistency, research_grounding, natural_writing, "
                  "recipient_appropriateness, call_to_action. Verdict 'reject' if any claim about the candidate is not in "
                  "CANDIDATE FACTS, any claim about the company is not in COMPANY RESEARCH, the email would fit another "
                  "company with the name swapped, it reads as flattery, or it reads as AI-written (staged contrasts, stock phrases, "
                  "generic praise, uniform rhythm, nothing only this candidate could say). List concrete issues.\n\n"
                  f"RECIPIENT (from our contact research): {contact.name + ', ' + (contact.title or '') if contact and contact.name else 'unknown'}\n\n"
                  f"EMAIL:\nSubject: {draft.subject}\n{body}\n\nCOMPANY RESEARCH:\n{_research(session, company)}\n\n"
                  f"CANDIDATE FACTS:\n{_facts(profile, cfg)}")
        try:
            review = llm.ask(provider, "qc", prompt, QCReview, retries=2, cache=False)
            scores = review.scores
            if review.verdict == "reject":
                issues += review.issues or ["rejected by reviewer"]
            else:
                warnings += review.issues
        except llm.ProviderError as e:
            warnings.append(f"LLM review unavailable ({str(e)[:80]}); deterministic checks only")
    draft.qc = {"passed": not issues, "issues": issues, "warnings": warnings, "scores": scores, "words": words}
    if draft.status in ("drafted", "reviewed"):
        draft.status = "drafted" if issues else "reviewed"
        if draft.kind == "initial":
            _advance(company, "drafted" if issues else "reviewed")
            if issues and company.state == "reviewed":
                db.transition(company, "drafted")
    session.commit()
    return draft.qc


def approve(session, draft, force=False, attachment=None) -> None:
    """Recipient-specific approval of exactly this text for exactly this address."""
    contact = draft.contact
    if contact is None or not contact.email or contact.email_status not in VERIFIED:
        raise ApprovalError("no verified email address for this recipient; run `outreach contact verify` first")
    if session.query(db.Suppression).filter_by(email=contact.email.lower()).first():
        raise ApprovalError(f"{contact.email} is suppressed (opt-out or negative reply)")
    if draft.status not in ("reviewed", "drafted") or (draft.status == "drafted" and not force):
        raise ApprovalError("draft has not passed quality review; fix it or approve with --force"
                            if draft.status == "drafted" else f"draft is {draft.status}, not awaiting approval")
    if attachment and not Path(attachment).expanduser().is_file():
        raise ApprovalError(f"attachment {attachment} not found; fix the path with `outreach config attachment <file>`")
    draft.status, draft.approved_at = "approved", db.now()
    draft.approved_hash = content_hash(draft.subject, draft.body, contact.email, attachment)
    if draft.kind == "initial":
        _advance(draft.company, "approved")
    session.commit()


def edit(session, draft, body=None, subject=None) -> bool:
    """Change a draft. Any change voids approval, so only the exact approved text can ever be sent."""
    if draft.status in ("sent", "superseded"):
        raise ApprovalError(f"a {draft.status} draft cannot be edited")
    body, subject = (body or draft.body).strip(), (subject or draft.subject).strip()
    if (body, subject) == (draft.body, draft.subject):
        return False
    draft.body, draft.subject = body, subject
    draft.approved_hash = draft.approved_at = draft.scheduled_at = None
    draft.status, draft.needs_refresh = "drafted", False
    if draft.kind == "initial" and draft.company.state in ("reviewed", "approved", "scheduled"):
        db.transition(draft.company, "drafted")
    session.commit()
    return True
