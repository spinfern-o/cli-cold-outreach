"""Guarded sending, reply tracking and follow-ups. Every send passes the same gate; nothing here drafts text."""
import hashlib
import logging
import re
import time
from datetime import timedelta
from email.utils import make_msgid

from . import db, gmail, outreach

log = logging.getLogger("companyscout.sender")
OPT_OUT = r"unsubscribe|remove me|take me off|do not (contact|email)|don't (contact|email)|stop (emailing|contacting)"
NEGATIVE = (r"not hiring|no internship|not interested|no openings|not (currently )?(offering|looking)|"
            r"(don't|do not) have (any )?(internship|opening)|unable to offer|not a fit")


class SendBlocked(RuntimeError):
    pass


def idempotency_key(draft) -> str:
    return hashlib.sha1(f"{draft.id}|{draft.approved_hash}".encode()).hexdigest()


def schedule(session, draft, when) -> None:
    if draft.status != "approved":
        raise outreach.ApprovalError("only an approved draft can be scheduled")
    draft.status, draft.scheduled_at = "scheduled", when
    if draft.kind == "initial":
        db.transition(draft.company, "scheduled")
    session.commit()


def send_approved(session, client, cfg, limit, now=None, sleep=time.sleep, only_scheduled=False):
    """Send (or, in draft-only mode, create Gmail drafts for) approved drafts. Returns [(draft, outcome)]."""
    if db.kv_get(session, "paused", False):
        raise SendBlocked("sending is paused (kill switch). Run `outreach resume` to re-enable.")
    now = now or db.now()
    ready = [d for d in session.query(db.Draft).filter(db.Draft.status.in_(("approved", "scheduled"))).order_by(db.Draft.id)
             if (d.status == "approved" and not only_scheduled) or (d.status == "scheduled" and d.scheduled_at <= now)]
    results = []
    for draft in ready[:limit]:
        sent_today = session.query(db.SentMessage).filter(db.SentMessage.status == "sent",
                                                          db.SentMessage.sent_at >= now - timedelta(hours=24)).count()
        if cfg["live_sending"] and sent_today >= cfg["daily_send_cap"]:
            results.append((draft, f"held: daily cap of {cfg['daily_send_cap']} reached"))
            continue
        last = session.query(db.SentMessage.sent_at).filter_by(status="sent").order_by(db.SentMessage.sent_at.desc()).first()
        if cfg["live_sending"] and last:  # conservative pacing, also across separate invocations
            wait = cfg["min_seconds_between_sends"] - (db.now() - last[0]).total_seconds()
            if wait > 0:
                sleep(wait)
        outcome = _send_one(session, client, cfg, draft, sleep)
        results.append((draft, outcome))
        log.info("send attempt", extra={"data": {"draft": draft.id, "outcome": outcome}})
    return results


def _send_one(session, client, cfg, draft, sleep) -> str:
    contact, company = draft.contact, draft.company
    if contact is None or not contact.email or contact.email_status not in outreach.VERIFIED:
        return "blocked: recipient has no verified email address"
    to = contact.email
    if draft.approved_hash != outreach.content_hash(draft.subject, draft.body, to):
        draft.status, draft.approved_hash, draft.approved_at = "drafted", None, None
        if draft.kind == "initial" and company.state in ("approved", "scheduled"):
            db.transition(company, "drafted")
        session.commit()
        return "blocked: content changed since approval; approve it again"
    if session.query(db.Suppression).filter_by(email=to.lower()).first():
        return "blocked: address is suppressed"
    key = idempotency_key(draft)
    row = session.query(db.SentMessage).filter_by(idempotency_key=key).first()
    if row is not None and row.status == "sent":
        return "skipped: already sent"
    if row is not None and row.status == "draft_created" and not cfg["live_sending"]:
        return "skipped: Gmail draft already created"
    resuming = row is not None and row.status in ("pending", "failed")
    if draft.kind == "initial" and not resuming:
        earlier = session.query(db.SentMessage).filter(db.SentMessage.status == "sent", db.SentMessage.kind == "initial",
                                                       (db.SentMessage.to_email == to) | (db.SentMessage.company_id == company.id)).first()
        if earlier is not None or client.sent_to(to):
            return "blocked: this address or company was already contacted"
    thread_id = in_reply_to = None
    if draft.kind == "followup":
        first = session.query(db.SentMessage).filter_by(company_id=company.id, kind="initial", status="sent").first()
        if first is None:
            return "blocked: no original message to follow up on"
        thread_id, in_reply_to = first.gmail_thread_id, first.rfc_message_id
    if row is None:  # write-ahead record: committed before any network call so a crash can never cause a blind resend
        row = db.SentMessage(draft_id=draft.id, company_id=company.id, kind=draft.kind, to_email=to, subject=draft.subject,
                             body=draft.body, idempotency_key=key, status="pending",
                             rfc_message_id=make_msgid(domain=client.address.split("@")[-1]))
        session.add(row)
        session.commit()
    msg = gmail.build_message(cfg["from_name"], client.address, to, draft.subject, draft.body, row.rfc_message_id, in_reply_to)
    if not cfg["live_sending"]:
        client.create_draft(msg, thread_id)
        row.status = "draft_created"
        session.commit()
        return "Gmail draft created (draft-only mode)"
    try:
        delivered = _deliver(client, msg, thread_id, row.rfc_message_id, resuming, sleep)
    except Exception as e:
        row.status, row.error = "pending" if isinstance(e, _Unknown) else "failed", str(e)[:500]
        session.commit()
        return f"failed: {str(e)[:120]} (safe to re-run; it will not double send)"
    row.gmail_message_id, row.gmail_thread_id = delivered["id"], delivered["threadId"]
    row.status, row.sent_at, row.error = "sent", db.now(), None
    draft.status = "sent"
    db.transition(company, "sent")
    db.log_activity(session, "sent", f"{company.name}: {draft.kind} email sent to {to}", company.id)
    session.commit()
    return "sent"


class _Unknown(RuntimeError):
    """Could not tell whether Gmail accepted the message."""


def _deliver(client, msg, thread_id, rfc_id, check_first, sleep):
    last = None
    for attempt in range(3):
        if check_first:  # after any failed or interrupted attempt, ask Gmail before sending again
            try:
                found = client.find_sent(rfc_id)
            except Exception as e:
                raise _Unknown(f"cannot confirm delivery state: {e}") from e
            if found:
                return found
            if attempt:
                sleep(2 ** attempt)
        try:
            return client.send(msg, thread_id)
        except Exception as e:
            last, check_first = e, True
    raise last


def classify(from_email, subject, text, headers) -> str:
    """shortcut: keyword triage of replies; anything unclear is a plain 'reply' for the user to read."""
    blob = f"{subject} {text}".lower()
    if re.search(r"mailer-daemon|postmaster", from_email.lower()) or headers.get("X-Failed-Recipients") \
            or re.search(r"delivery status notification|undeliverable|delivery has failed", blob):
        return "bounce"
    if headers.get("Auto-Submitted", "no").lower() != "no" or re.search(r"automatic reply|out of (the )?office", blob):
        return "auto"
    if re.search(OPT_OUT, blob):
        return "opt_out"
    if re.search(NEGATIVE, blob):
        return "negative"
    return "reply"


def _suppress(session, email, reason):
    if email and not session.query(db.Suppression).filter_by(email=email.lower()).first():
        session.add(db.Suppression(email=email.lower(), reason=reason))


def sync_replies(session, client, cfg, now=None):
    """Read only the threads this tool started; record new replies and apply their consequences."""
    now = now or db.now()
    rows = session.query(db.SentMessage).filter(db.SentMessage.status == "sent", db.SentMessage.gmail_thread_id.isnot(None)).all()
    ours = {r.gmail_message_id for r in rows}
    new = []
    for row in {r.gmail_thread_id: r for r in rows if r.kind == "initial"}.values():
        company = session.get(db.Company, row.company_id)
        if company is None or company.state not in ("sent", "followup_pending", "replied"):
            continue
        for m in client.thread(row.gmail_thread_id):
            if m["id"] in ours or m["from"].lower() == client.address.lower() \
                    or session.query(db.Reply).filter_by(gmail_message_id=m["id"]).first():
                continue
            kind = classify(m["from"], m.get("subject", ""), m.get("snippet", ""), m.get("headers", {}))
            reply = db.Reply(sent_id=row.id, company_id=company.id, gmail_message_id=m["id"], from_email=m["from"],
                             snippet=m.get("snippet", ""), classification=kind)
            session.add(reply)
            new.append(reply)
            if kind == "auto":
                continue
            for f in session.query(db.Draft).filter(db.Draft.company_id == company.id, db.Draft.kind == "followup",
                                                    db.Draft.status.in_(("drafted", "reviewed", "approved", "scheduled"))):
                f.status = "superseded"  # a reply of any kind stops follow-ups
            if kind == "bounce":
                contact = next((k for k in company.contacts if k.email == row.to_email), None)
                if contact:
                    contact.email_status = "bounced"
                _suppress(session, row.to_email, "bounce")
                db.transition(company, "bounced")
            elif kind == "opt_out":
                _suppress(session, row.to_email, "opt_out")
                db.transition(company, "opted_out")
            elif kind == "negative":
                _suppress(session, row.to_email, "negative")
                db.transition(company, "rejected")
            elif company.state != "replied":
                db.transition(company, "replied")
            db.log_activity(session, "reply", f"{company.name}: {kind} from {m['from']}", company.id)
        session.commit()
    for row in rows:  # no answer two weeks after the follow-up: stop tracking
        company = session.get(db.Company, row.company_id)
        if row.kind == "followup" and company and company.state == "sent" and row.sent_at <= now - timedelta(days=14):
            db.transition(company, "closed")
    session.commit()
    return new


def prepare_followups(session, cfg, now=None):
    """Create follow-up drafts that are due. They wait for their own approval; nothing is sent here."""
    now = now or db.now()
    created = []
    for row in session.query(db.SentMessage).filter_by(kind="initial", status="sent"):
        company = session.get(db.Company, row.company_id)
        original = session.get(db.Draft, row.draft_id)
        if company is None or company.state != "sent" or original is None or not original.followup_body:
            continue
        if row.sent_at > now - timedelta(days=cfg["followup_days"]):
            continue
        if session.query(db.Draft).filter_by(company_id=company.id, kind="followup").first() \
                or session.query(db.Suppression).filter_by(email=row.to_email.lower()).first():
            continue
        f = db.Draft(company_id=company.id, contact_id=original.contact_id, kind="followup", subject=f"Re: {row.subject}",
                     subjects=[f"Re: {row.subject}"], body=original.followup_body, status="reviewed")
        session.add(f)
        db.transition(company, "followup_pending")
        created.append(f)
    session.commit()
    return created
