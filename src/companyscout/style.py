"""Deterministic writing checks for cold emails: stock phrases, AI-writing tells, format. Shared by QC and the
cold-email skill's checker (.claude/skills/cold-email/scripts/check_email.py)."""
import re
import statistics

STOCK = ["hope this email finds you well", "hope this finds you well", "i'm reaching out", "i am reaching out",
         "i came across", "passionate about", "i'm excited to", "i am excited to", "would love the opportunity",
         "thrilled", "leverage", "synergy", "valuable insights", "quick chat", "time and consideration",
         "i am writing to", "i'm writing to", "dream company", "great fit", "esteemed", "cutting-edge",
         "innovative company"]
AI_WORDS = ["delve", "tapestry", "testament", "pivotal", "crucial", "intricate", "meticulous", "vibrant", "landscape",
            "underscore", "boasts", "showcase", "foster", "enhance", "robust", "multifaceted", "seamless",
            "innovative", "game-changer", "impressive", "serves as", "stands as", "additionally", "furthermore"]
STAGING = [r"\bnot (just|only|merely)\b[^.?!]{0,80}\bbut\b", r"\bit'?s not\b[^.?!]{0,60}[,;.—-]\s*it'?s\b",
           r"\bisn'?t (about|just)\b[^.?!]{0,60}[.?!]\s*it'?s\b"]
SHARED_INBOXES = {"careers", "career", "jobs", "job", "hr", "hello", "hi", "info", "contact", "talent", "recruiting",
                  "recruit", "join", "connect", "team", "office", "admin", "general", "inquiries", "people", "work"}


def shared_inbox(email) -> bool:
    """careers@, hello@, info@ and the like: read by whoever triages it, so the email must be forwardable."""
    return bool(email) and email.split("@")[0].lower() in SHARED_INBOXES


def check(subject: str, body: str) -> tuple[list[str], list[str]]:
    """(issues, warnings). Issues should block approval; warnings are judgement calls. Word count is checked by QC."""
    issues, warnings, low = [], [], body.lower()
    if len(subject) >= 60:
        issues.append(f"subject is {len(subject)} characters; keep it under 60")
    if body.count("?") > 2:
        issues.append(f"{body.count('?')} questions; one clear ask")
    if len(re.findall(r"https?://|www\.|github\.com/", body)) > 1:
        warnings.append("more than one link")
    stock = [p for p in STOCK if p in low]
    if stock:
        issues.append(f"stock phrasing: {', '.join(stock)}")
    if any(re.search(p, low) for p in STAGING):
        issues.append("'not X but Y' / 'it's not X, it's Y' staging reads as AI-written")
    if re.search(r"^\s*([-*•]|\d+\.)\s", body, re.M) or "**" in body:
        issues.append("bullets or bold in a plain-text email")
    ai = [w for w in AI_WORDS if re.search(rf"\b{re.escape(w)}", low)]
    if ai:
        warnings.append(f"AI-flavoured wording: {', '.join(ai)}")
    dashes = body.count("—") + body.count(" -- ")
    if dashes > 1:
        warnings.append(f"{dashes} em dashes")
    lens = [len(s.split()) for s in re.split(r"(?<=[.?!])\s+", re.sub(r"\s+", " ", body)) if len(s.split()) > 2]
    if len(lens) >= 4 and statistics.pstdev(lens) < 4:
        warnings.append(f"sentence lengths too uniform ({min(lens)}-{max(lens)} words)")
    return issues, warnings
