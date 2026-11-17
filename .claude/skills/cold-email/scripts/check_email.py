#!/usr/bin/env python3
"""Lint a cold email: line 1 = subject, blank line, then body. Usage: check_email.py FILE [--min 90 --max 140] | --selftest
Uses the same rules as CompanyScout's QC (src/companyscout/style.py), so a pass here means the deterministic QC passes too."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "src"))  # repo/src: .claude/skills/cold-email/scripts -> repo
from companyscout import style  # noqa: E402


def check(text, lo=90, hi=140):
    lines = text.strip().split("\n")
    subject, body = lines[0].removeprefix("Subject:").strip(), "\n".join(lines[1:]).strip()
    words = len(body.split())
    out = [("FAIL" if not lo <= words <= hi else "OK", f"{words} words (target {lo}-{hi})"),
           ("OK", f"subject {len(subject)} chars")]
    if "?" not in body:
        out.append(("FAIL", "no question; end with one clear ask"))
    issues, warnings = style.check(subject, body)
    return out + [("FAIL", m) for m in issues] + [("WARN", m) for m in warnings]


def main(argv):
    if argv[1:] == ["--selftest"]:
        bad = ("Subject: Exciting opportunity to collaborate on your innovative platform today\n\n"
               "I hope this email finds you well. I'm reaching out because I'm passionate about AI. "
               "It's not just a dashboard, but a way to build trust. Would love the opportunity to chat?")
        fails = " | ".join(m for lv, m in check(bad) if lv == "FAIL")
        assert all(k in fails for k in ("stock phrasing", "staging", "subject is", "words")), fails
        good = "Subject: Question on Insights numbers\n\nHi Kate,\n\n" + ("Short one. " + "word " * 20 + "here. ") * 4 \
               + "Is a Summer 2027 intern something your team would consider?\n\nLarry Wang"
        assert not [m for lv, m in check(good) if lv == "FAIL"], check(good)
        assert style.shared_inbox("careers@acme.com") and not style.shared_inbox("dana@acme.com")
        print("selftest ok")
        return 0
    lo = int(argv[argv.index("--min") + 1]) if "--min" in argv else 90
    hi = int(argv[argv.index("--max") + 1]) if "--max" in argv else 140
    results = check(Path(argv[1]).read_text(), lo, hi)
    for level, msg in results:
        print(f"{level:4}  {msg}")
    fails = sum(lv == "FAIL" for lv, _ in results)
    print(f"\n{fails} FAIL, {sum(lv == 'WARN' for lv, _ in results)} WARN")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
