#!/usr/bin/env python3
"""Lint a cold email: line 1 = subject, blank line, then body. Usage: check_email.py FILE [--min 90 --max 140] | --selftest"""
import re
import statistics
import sys

STOCK = ["hope this email finds you well", "hope this finds you well", "i'm reaching out", "i am reaching out",
         "i came across", "passionate about", "i'm excited to", "i am excited to", "would love the opportunity",
         "thrilled", "leverage", "synergy", "valuable insights", "quick chat", "time and consideration",
         "i am writing to", "i'm writing to", "dream company", "great fit", "esteemed", "cutting-edge"]
AI_WORDS = ["delve", "tapestry", "testament", "pivotal", "crucial", "intricate", "meticulous", "vibrant", "landscape",
            "underscore", "boasts", "showcase", "foster", "enhance", "robust", "multifaceted", "seamless",
            "innovative", "game-changer", "impressive", "serves as", "stands as", "additionally", "furthermore"]
STAGING = [r"\bnot (just|only|merely)\b[^.?!]{0,80}\bbut\b", r"\bit'?s not\b[^.?!]{0,60}[,;.—-]\s*it'?s\b",
           r"\bisn'?t (about|just)\b[^.?!]{0,60}[.?!]\s*it'?s\b"]


def check(text, lo=90, hi=140):
    lines = text.strip().split("\n")
    subject, body = lines[0].removeprefix("Subject:").strip(), "\n".join(lines[1:]).strip()
    low, out = body.lower(), []
    add = lambda level, msg: out.append((level, msg))
    words = len(body.split())
    add("FAIL" if not lo <= words <= hi else "OK", f"{words} words (target {lo}-{hi})")
    add("FAIL" if len(subject) >= 60 else "WARN" if len(subject) > 45 else "OK", f"subject {len(subject)} chars")
    q = body.count("?")
    add("FAIL" if q == 0 or q > 2 else "OK", f"{q} question mark(s); want one clear ask")
    links = len(re.findall(r"https?://|www\.|github\.com/", body))
    add("WARN" if links > 1 else "OK", f"{links} link(s)")
    for p in STOCK:
        if p in low:
            add("FAIL", f"stock phrase: '{p}'")
    for w in AI_WORDS:
        if re.search(rf"\b{re.escape(w)}", low):
            add("WARN", f"AI-flavoured word: '{w}'")
    for pat in STAGING:
        if re.search(pat, low):
            add("FAIL", "'not X but Y' / 'it's not X, it's Y' staging")
    dashes = body.count("—") + body.count(" -- ")
    if dashes > 1:
        add("WARN", f"{dashes} em dashes")
    if re.search(r"^\s*([-*•]|\d+\.)\s", body, re.M) or "**" in body:
        add("FAIL", "bullets/bold in a plain-text email")
    sents = [s for s in re.split(r"(?<=[.?!])\s+", re.sub(r"\s+", " ", body)) if len(s.split()) > 2]
    lens = [len(s.split()) for s in sents]
    if len(lens) >= 4 and statistics.pstdev(lens) < 4:
        add("WARN", f"sentence lengths too uniform ({min(lens)}-{max(lens)} words); mix short and long")
    return out


def main(argv):
    if argv[1:] == ["--selftest"]:
        bad = ("Subject: Exciting opportunity to collaborate on your innovative platform today\n\n"
               "I hope this email finds you well. I'm reaching out because I'm passionate about AI. "
               "It's not just a dashboard, but a way to build trust. Would love the opportunity to chat?")
        levels = {m for lv, m in check(bad) if lv == "FAIL"}
        assert any("stock phrase" in m for m in levels) and any("staging" in m for m in levels)
        assert any(m.startswith("subject") for m in levels) and any("words" in m for m in levels)
        good = "Subject: Question on Insights numbers\n\nHi Kate,\n\n" + ("Short one. " + "word " * 20 + "here. ") * 4 \
               + "Is a Summer 2027 intern something your team would consider?\n\nLarry Wang"
        assert not [m for lv, m in check(good) if lv == "FAIL"], check(good)
        print("selftest ok")
        return 0
    lo = int(argv[argv.index("--min") + 1]) if "--min" in argv else 90
    hi = int(argv[argv.index("--max") + 1]) if "--max" in argv else 140
    results = check(open(argv[1]).read(), lo, hi)
    for level, msg in results:
        print(f"{level:4}  {msg}")
    fails = sum(lv == "FAIL" for lv, _ in results)
    print(f"\n{fails} FAIL, {sum(lv == 'WARN' for lv, _ in results)} WARN")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
