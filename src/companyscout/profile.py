"""Candidate knowledge base: cleaned resume facts and deterministic project-to-company matching."""
from collections import Counter
from pathlib import Path

from .schemas import CandidateProfile


def extract_text(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        from pypdf import PdfReader
        return "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
    return path.read_text(errors="replace")


def clean(raw: CandidateProfile, source_text: str, cfg) -> CandidateProfile:
    """Keep only what the source documents support: drop excluded experiences and technologies not named in the text."""
    text = source_text.lower()
    excluded = [e.lower() for e in cfg["exclude_experiences"]]
    projects = []
    for p in raw.projects:
        if any(e in p.name.lower() for e in excluded):
            continue
        projects.append(p.model_copy(update={"technologies": [t for t in p.technologies if t.lower() in text]}))
    return raw.model_copy(update={"projects": projects, "source_text": source_text})


def match(profile: CandidateProfile, company, usage: Counter) -> tuple[str, float, str]:
    """Pick the one project that makes the candidate credible for this company: (name, score 0-100, rationale).

    `usage` counts how often each project already leads a draft, so ties rotate instead of one project leading everything.
    """
    tags = {t.lower() for t in company.tags or []}
    stack = {s.lower() for s in company.stack or []}
    total = sum(usage.values()) or 1
    best = ("", 0.0, "No overlap found; lead with general engineering fit.")
    best_adjusted = -1.0
    for p in profile.projects:
        shared_tags = sorted(tags & set(p.tags))
        shared_tech = sorted(t for t in p.technologies if t.lower() in stack)
        raw = min(100.0, 30 * len(shared_tags) + 12 * len(shared_tech))
        adjusted = raw * (1 - 0.3 * usage[p.name] / total)
        if adjusted > best_adjusted:
            why = f"shared areas: {', '.join(shared_tags) or 'none'}; shared stack: {', '.join(shared_tech) or 'none'}"
            best, best_adjusted = (p.name, raw, why), adjusted
    return best


def import_profile(session, provider, cfg, source_text: str) -> CandidateProfile:
    from . import db, llm
    from .schemas import TAGS
    prompt = ("TASK: profile\nBuild a candidate knowledge base from the documents below. Use only facts stated in them; "
              "do not add metrics, technologies, employers or projects. One entry in `projects` per project or work "
              "experience (kind = project or experience). `evidence` = the supporting bullets, close to verbatim. "
              f"`tags` only from: {', '.join(TAGS)}. `credentials` = notable honours.\n\nDOCUMENTS:\n{source_text}")
    cleaned = clean(llm.ask(provider, "profile", prompt, CandidateProfile, session=session, cfg=cfg), source_text, cfg)
    db.kv_set(session, "profile", cleaned.model_dump())
    return cleaned


def load_profile(session):
    from . import db
    data = db.kv_get(session, "profile")
    return CandidateProfile.model_validate(data) if data else None
