"""Settings: defaults < ~/.companyscout/config.json < OUTREACH_<KEY> environment variables."""
import json
import logging
import os
from pathlib import Path

DEFAULTS = {
    "provider": "claude",            # "claude" (local Claude Code CLI) or "mock"
    "claude_path": "claude",
    "claude_model": "sonnet",
    "claude_timeout_s": 600,
    "gmail": "real",                 # "real" or "mock"
    "gmail_scopes": [
        "https://www.googleapis.com/auth/gmail.compose",   # create drafts + send
        "https://www.googleapis.com/auth/gmail.readonly",  # read threads we started (replies, bounces)
    ],
    "from_name": "Ziheng (Larry) Wang",
    "size_min": 15,
    "size_max": 750,
    "locations": ["United States", "Remote", "Michigan", "Midwest"],
    "exclude_experiences": ["Michigan Dining", "MDining"],
    "credentials": ["8VC Fellowship finalist"],  # candidate-stated facts that are not on the resume
    "weights": {"tech_match": 25, "access": 20, "stability": 20, "intern_potential": 20, "timing": 10, "interest": 5},
    "shortlist_interval_h": 12,
    "tracked_interval_h": 48,
    "discovery_interval_h": 168,
    "monitor_tick_s": 3600,
    "monitor_max_per_tick": 15,
    "weekly_discovery_limit": 15,    # 0 disables background discovery
    "news_significant": 60,          # significance at which news rescoring flags drafts
    "notify": True,                  # macOS notification for significant findings
    "research_parallel": 3,
    "cache_ttl_h": 24,
    "live_sending": False,           # draft-only until the user turns this on
    "daily_send_cap": 5,
    "min_seconds_between_sends": 90,
    "followup_days": 6,
    "word_range": [90, 140],
}


def home() -> Path:
    p = Path(os.environ.get("OUTREACH_HOME", "~/.companyscout")).expanduser()
    p.mkdir(parents=True, exist_ok=True)
    return p


def load() -> dict:
    cfg = dict(DEFAULTS)
    f = home() / "config.json"
    if f.exists():
        cfg.update(json.loads(f.read_text()))
    for key in DEFAULTS:
        raw = os.environ.get(f"OUTREACH_{key.upper()}")
        if raw is not None:
            try:
                cfg[key] = json.loads(raw)
            except ValueError:
                cfg[key] = raw
    return cfg


def save(updates: dict) -> None:
    f = home() / "config.json"
    current = json.loads(f.read_text()) if f.exists() else {}
    current.update(updates)
    f.write_text(json.dumps(current, indent=2))


class _JsonFormatter(logging.Formatter):
    def format(self, record):
        out = {"ts": self.formatTime(record), "level": record.levelname, "logger": record.name, "msg": record.getMessage()}
        out.update(getattr(record, "data", {}))
        return json.dumps(out, default=str)


def setup_logging() -> None:
    root = logging.getLogger("companyscout")
    if root.handlers:
        return
    (home() / "logs").mkdir(exist_ok=True)
    h = logging.FileHandler(home() / "logs" / "outreach.log")
    h.setFormatter(_JsonFormatter())
    root.addHandler(h)
    root.setLevel(logging.INFO)
