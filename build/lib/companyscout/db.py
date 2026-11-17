"""SQLite persistence: models, migrations, pipeline state machine, key-value store."""
from datetime import datetime, timezone

from sqlalchemy import (JSON, Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text,
                        create_engine, event)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

from . import config

Base = declarative_base()


def now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)  # naive UTC everywhere; SQLite drops tz


class Company(Base):
    __tablename__ = "companies"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    domain = Column(String, nullable=False, unique=True)  # canonical domain = dedupe key
    website = Column(String)
    industry = Column(String)
    description = Column(Text)
    employee_count = Column(Integer)
    employee_source = Column(String)
    employee_verified_at = Column(DateTime)
    location = Column(String)
    remote = Column(Boolean)
    founded_year = Column(Integer)
    stability = Column(String)             # Strong Evidence | Moderate Evidence | Uncertain | Elevated Risk
    stability_confidence = Column(Float)
    stack = Column(JSON, default=list)
    tags = Column(JSON, default=list)
    hiring = Column(String)                # intern_evidence | hiring_eng | none | unknown
    research = Column(JSON)                # full validated CompanyResearch
    sources = Column(JSON, default=list)
    feed_url = Column(String)
    state = Column(String, nullable=False, default="discovered", server_default="discovered")
    shortlisted = Column(Boolean, default=False)
    hidden = Column(Boolean, default=False)
    excluded_reason = Column(String)
    notes = Column(Text)
    user_rank = Column(Integer)
    interest = Column(Integer, default=50)  # 0-100, set with `outreach rate`
    score = Column(Float)                   # attractiveness
    evidence_confidence = Column(Float)     # how well-sourced the research is
    breakdown = Column(JSON)
    risk_flags = Column(JSON, default=list)
    primary_project = Column(String)
    match_rationale = Column(Text)
    discovered_at = Column(DateTime, default=now)
    researched_at = Column(DateTime)
    news_checked_at = Column(DateTime)
    contacts = relationship("Contact", backref="company", cascade="all, delete-orphan")


class Contact(Base):
    __tablename__ = "contacts"
    id = Column(Integer, primary_key=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False)
    name = Column(String)
    title = Column(String)
    email = Column(String)
    email_status = Column(String, default="unknown")  # verified_public | user_verified | inferred | unknown
    email_source_url = Column(String)
    profile_url = Column(String)
    verified_at = Column(DateTime)


class NewsItem(Base):
    __tablename__ = "news"
    id = Column(Integer, primary_key=True)
    company_id = Column(Integer, ForeignKey("companies.id"))  # NULL = general market news
    dedupe_key = Column(String, nullable=False, unique=True)
    title = Column(String, nullable=False)
    url = Column(String)
    summary = Column(Text)
    category = Column(String)  # product | funding | hiring | acquisition | leadership | layoffs | risk | other
    significance = Column(Integer, default=0)
    published_at = Column(DateTime)
    fetched_at = Column(DateTime, default=now)
    source = Column(String)
    verified = Column(Boolean, default=False)  # only verified items may change scores, risk flags or drafts
    suggested_opening = Column(Text)
    company = relationship("Company")


class History(Base):
    __tablename__ = "history"
    id = Column(Integer, primary_key=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False)
    field = Column(String)
    old = Column(Text)
    new = Column(Text)
    source = Column(String)
    at = Column(DateTime, default=now)


class Activity(Base):
    __tablename__ = "activity"
    id = Column(Integer, primary_key=True)
    at = Column(DateTime, default=now)
    kind = Column(String)
    company_id = Column(Integer)
    message = Column(Text)


class Draft(Base):
    __tablename__ = "drafts"
    id = Column(Integer, primary_key=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False)
    contact_id = Column(Integer, ForeignKey("contacts.id"))
    kind = Column(String, default="initial")  # initial | followup
    subjects = Column(JSON, default=list)
    subject = Column(String)
    body = Column(Text)
    rationale = Column(Text)
    followup_body = Column(Text)
    qc = Column(JSON)
    status = Column(String, default="drafted")  # drafted | reviewed | approved | scheduled | sent | rejected | superseded
    approved_hash = Column(String)
    approved_at = Column(DateTime)
    scheduled_at = Column(DateTime)
    needs_refresh = Column(Boolean, default=False)
    refresh_suggestion = Column(Text)
    created_at = Column(DateTime, default=now)
    company = relationship("Company")
    contact = relationship("Contact")


class SentMessage(Base):
    __tablename__ = "sent_messages"
    id = Column(Integer, primary_key=True)
    draft_id = Column(Integer)
    company_id = Column(Integer)
    kind = Column(String, default="initial")
    to_email = Column(String, nullable=False)
    subject = Column(String)
    body = Column(Text)
    idempotency_key = Column(String, nullable=False, unique=True)
    rfc_message_id = Column(String)
    gmail_message_id = Column(String)
    gmail_thread_id = Column(String)
    status = Column(String, default="pending")  # pending | sent | draft_created | failed
    error = Column(Text)
    created_at = Column(DateTime, default=now)
    sent_at = Column(DateTime)


class Reply(Base):
    __tablename__ = "replies"
    id = Column(Integer, primary_key=True)
    sent_id = Column(Integer, ForeignKey("sent_messages.id"))
    company_id = Column(Integer)
    gmail_message_id = Column(String, unique=True)
    from_email = Column(String)
    snippet = Column(Text)
    classification = Column(String)  # reply | negative | opt_out | bounce | auto
    received_at = Column(DateTime, default=now)


class Suppression(Base):
    __tablename__ = "suppressions"
    id = Column(Integer, primary_key=True)
    email = Column(String, nullable=False, unique=True)
    reason = Column(String)
    at = Column(DateTime, default=now)


class MonitorRun(Base):
    __tablename__ = "monitor_runs"
    id = Column(Integer, primary_key=True)
    started_at = Column(DateTime, default=now)
    finished_at = Column(DateTime)
    checked = Column(Integer, default=0)
    new_items = Column(Integer, default=0)
    missed_ticks = Column(Integer, default=0)
    error = Column(Text)


class KV(Base):
    __tablename__ = "kv"
    key = Column(String, primary_key=True)
    value = Column(JSON)


# --- migrations: PRAGMA user_version counts applied entries of MIGRATIONS ---

def _m1_base(conn):
    Base.metadata.create_all(conn)
    for op in ("UPDATE", "DELETE"):
        conn.exec_driver_sql(
            f"CREATE TRIGGER IF NOT EXISTS sent_immutable_{op.lower()} BEFORE {op} ON sent_messages "
            "WHEN OLD.status = 'sent' BEGIN SELECT RAISE(ABORT, 'sent messages are immutable'); END")


def _m2_user_rank(conn):
    cols = [r[1] for r in conn.exec_driver_sql("PRAGMA table_info(companies)")]
    if "user_rank" not in cols:
        conn.exec_driver_sql("ALTER TABLE companies ADD COLUMN user_rank INTEGER")


MIGRATIONS = [_m1_base, _m2_user_rank]


def migrate(engine) -> None:
    with engine.begin() as conn:
        version = conn.exec_driver_sql("PRAGMA user_version").scalar()
        for i, step in enumerate(MIGRATIONS[version:], start=version + 1):
            step(conn)
            conn.exec_driver_sql(f"PRAGMA user_version={i}")


def make_engine(path=None):
    engine = create_engine(f"sqlite:///{path or config.home() / 'outreach.db'}")

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi, _):
        dbapi.execute("PRAGMA journal_mode=WAL")   # CLI and the launchd monitor may run at once
        dbapi.execute("PRAGMA busy_timeout=30000")
    return engine


def connect(path=None):
    engine = make_engine(path)
    migrate(engine)
    return sessionmaker(bind=engine)()


# --- pipeline state machine ---

TERMINAL = {"closed", "unavailable", "opted_out", "rejected", "bounced"}
TRANSITIONS = {
    "discovered": {"researched"},
    "researched": {"shortlisted", "drafted"},
    "shortlisted": {"researched", "drafted"},
    "drafted": {"reviewed", "shortlisted", "drafted"},
    "reviewed": {"approved", "drafted", "shortlisted"},
    "approved": {"scheduled", "sent", "drafted"},
    "scheduled": {"sent", "approved", "drafted"},
    "sent": {"replied", "followup_pending", "bounced"},
    "followup_pending": {"sent", "replied", "bounced"},
    "replied": {"rejected", "opted_out"},
}


class InvalidTransition(ValueError):
    pass


def transition(company, state: str) -> None:
    allowed = TRANSITIONS.get(company.state or "discovered", set()) | ({"closed", "unavailable"} if company.state not in TERMINAL else set())
    if company.state in ("sent", "followup_pending", "replied"):
        allowed |= {"opted_out", "rejected"}
    if state not in allowed:
        raise InvalidTransition(f"{company.name}: {company.state} -> {state} is not allowed")
    company.state = state


def kv_get(session, key, default=None):
    row = session.get(KV, key)
    return default if row is None else row.value


def kv_set(session, key, value) -> None:
    session.merge(KV(key=key, value=value))
    session.commit()


def log_activity(session, kind, message, company_id=None) -> None:
    session.add(Activity(kind=kind, message=message, company_id=company_id))
