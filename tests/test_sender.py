from datetime import timedelta

import pytest

from companyscout import db, gmail, outreach, sender

NOSLEEP = lambda s: None


def approved(session, name="Acme", email="dana@acme.com", status="verified_public"):
    c = db.Company(name=name, domain=f"{name.lower()}.com", state="approved")
    k = db.Contact(company=c, name="Dana", email=email, email_status=status)
    session.add_all([c, k])
    session.commit()
    d = db.Draft(company_id=c.id, contact_id=k.id, subject=f"Hello {name}", body=f"Body for {name}", status="approved",
                 followup_body=f"Following up, {name}", approved_hash=outreach.content_hash(f"Hello {name}", f"Body for {name}", email))
    session.add(d)
    session.commit()
    return c, d


def live(cfg, **kw):
    return {**cfg, "live_sending": True, "min_seconds_between_sends": 0, **kw}


def run(session, client, cfg, **kw):
    return sender.send_approved(session, client, cfg, limit=10, sleep=NOSLEEP, **kw)


def test_live_send_delivers_once_and_records_it(session, cfg):
    c, d = approved(session)
    client = gmail.MockGmail()
    run(session, client, live(cfg))
    row = session.query(db.SentMessage).one()
    assert (row.status, d.status, c.state) == ("sent", "sent", "sent")
    assert [m["to"] for m in client.box("sent")] == ["dana@acme.com"] and row.gmail_thread_id


def test_draft_only_mode_creates_gmail_draft_and_sends_nothing(session, cfg):
    c, d = approved(session)
    client = gmail.MockGmail()
    run(session, client, cfg)
    run(session, client, cfg)
    assert client.box("sent") == [] and len(client.box("draft")) == 1 and d.status == "approved"


def test_second_initial_email_to_same_address_is_blocked(session, cfg):
    approved(session)
    _, again = approved(session, name="Other", email="dana@acme.com")
    client = gmail.MockGmail()
    results = dict((d.id, why) for d, why in run(session, client, live(cfg)))
    assert len(client.box("sent")) == 1 and "already" in results[again.id]


def test_address_already_in_gmail_sent_folder_is_blocked(session, cfg):
    _, d = approved(session)
    client = gmail.MockGmail()
    client.send(gmail.build_message("L", client.address, "dana@acme.com", "Earlier manual note", "hi", "<manual@example.com>"))
    assert "already" in run(session, client, live(cfg))[0][1] and len(client.box("sent")) == 1


def test_body_changed_after_approval_is_not_sent(session, cfg):
    _, d = approved(session)
    d.body = "Tampered body"
    session.commit()
    client = gmail.MockGmail()
    assert "changed" in run(session, client, live(cfg))[0][1]
    assert client.box("sent") == [] and (d.status, d.approved_hash) == ("drafted", None)


def test_address_downgraded_to_unverified_is_not_sent(session, cfg):
    _, d = approved(session)
    d.contact.email_status = "inferred"
    session.commit()
    client = gmail.MockGmail()
    assert "verified" in run(session, client, live(cfg))[0][1] and client.box("sent") == []


def test_kill_switch_blocks_all_sending(session, cfg):
    approved(session)
    db.kv_set(session, "paused", True)
    client = gmail.MockGmail()
    with pytest.raises(sender.SendBlocked):
        run(session, client, live(cfg))
    assert client.box("sent") == [] and client.box("draft") == []


def test_daily_cap_is_enforced(session, cfg):
    for n in ("A", "B", "C"):
        approved(session, name=n, email=f"x@{n.lower()}.com")
    client = gmail.MockGmail()
    run(session, client, live(cfg, daily_send_cap=2))
    assert len(client.box("sent")) == 2
    run(session, client, live(cfg, daily_send_cap=2))
    assert len(client.box("sent")) == 2


def test_lost_response_is_recovered_without_a_second_send(session, cfg):
    approved(session)

    class LostResponse(gmail.MockGmail):
        def send(self, msg, thread_id=None):
            super().send(msg, thread_id)
            raise ConnectionError("response lost")

    client = LostResponse()
    run(session, client, live(cfg))
    assert len(client.box("sent")) == 1 and session.query(db.SentMessage).one().status == "sent"


def test_transient_failure_is_retried_and_sent_once(session, cfg):
    approved(session)

    class Flaky(gmail.MockGmail):
        fails = 1

        def send(self, msg, thread_id=None):
            if Flaky.fails:
                Flaky.fails -= 1
                raise ConnectionError("503")
            return super().send(msg, thread_id)

    client = Flaky()
    run(session, client, live(cfg))
    assert len(client.box("sent")) == 1 and session.query(db.SentMessage).one().status == "sent"


def test_interrupted_send_resumes_from_its_pending_record(session, cfg):
    c, d = approved(session)
    client = gmail.MockGmail()
    client.send(gmail.build_message("L", client.address, "dana@acme.com", d.subject, d.body, "<crashed@example.com>"))
    session.add(db.SentMessage(draft_id=d.id, company_id=c.id, to_email="dana@acme.com", subject=d.subject, body=d.body,
                               idempotency_key=sender.idempotency_key(d), rfc_message_id="<crashed@example.com>", status="pending"))
    session.commit()
    run(session, client, live(cfg))
    assert len(client.box("sent")) == 1 and d.status == "sent"


def test_scheduled_draft_waits_for_its_time(session, cfg):
    c, d = approved(session)
    sender.schedule(session, d, db.now() + timedelta(hours=3))
    client = gmail.MockGmail()
    run(session, client, live(cfg))
    assert client.box("sent") == [] and c.state == "scheduled"
    run(session, client, live(cfg), now=db.now() + timedelta(hours=4))
    assert len(client.box("sent")) == 1


def sent(session, cfg, client):
    c, d = approved(session)
    run(session, client, live(cfg))
    return c, d, session.query(db.SentMessage).one()


def test_reply_marks_company_replied_and_cancels_follow_up(session, cfg):
    client = gmail.MockGmail()
    c, d, row = sent(session, cfg, client)
    later = db.now() + timedelta(days=10)
    assert len(sender.prepare_followups(session, cfg, later)) == 1
    client.add_reply(row.gmail_thread_id, "dana@acme.com", "Thanks Larry, happy to chat next week.")
    replies = sender.sync_replies(session, client, cfg)
    assert [r.classification for r in replies] == ["reply"] and c.state == "replied"
    assert session.query(db.Draft).filter_by(kind="followup").one().status == "superseded"
    assert sender.prepare_followups(session, cfg, later) == [] and sender.sync_replies(session, client, cfg) == []


def test_opt_out_reply_suppresses_the_address(session, cfg):
    client = gmail.MockGmail()
    c, d, row = sent(session, cfg, client)
    client.add_reply(row.gmail_thread_id, "dana@acme.com", "Please remove me from your list.")
    sender.sync_replies(session, client, cfg)
    assert c.state == "opted_out" and session.query(db.Suppression).one().email == "dana@acme.com"
    _, again = approved(session, name="Other", email="dana@acme.com")
    again.status = "reviewed"
    with pytest.raises(outreach.ApprovalError, match="suppressed"):
        outreach.approve(session, again)


def test_negative_reply_marks_rejected_and_suppresses(session, cfg):
    client = gmail.MockGmail()
    c, d, row = sent(session, cfg, client)
    client.add_reply(row.gmail_thread_id, "dana@acme.com", "Sorry, we are not hiring interns this year.")
    sender.sync_replies(session, client, cfg)
    assert c.state == "rejected" and session.query(db.Suppression).count() == 1


def test_bounce_marks_company_bounced(session, cfg):
    client = gmail.MockGmail()
    c, d, row = sent(session, cfg, client)
    client.add_reply(row.gmail_thread_id, "mailer-daemon@googlemail.com", "Address not found", subject="Delivery Status Notification (Failure)")
    sender.sync_replies(session, client, cfg)
    assert c.state == "bounced" and d.contact.email_status == "bounced"


def test_follow_up_needs_its_own_approval_and_sends_in_thread(session, cfg):
    client = gmail.MockGmail()
    c, d, row = sent(session, cfg, client)
    assert sender.prepare_followups(session, cfg, db.now() + timedelta(days=2)) == []
    (f,) = sender.prepare_followups(session, cfg, db.now() + timedelta(days=10))
    assert (f.status, c.state) == ("reviewed", "followup_pending")
    run(session, client, live(cfg))
    assert len(client.box("sent")) == 1  # not approved yet
    outreach.approve(session, f)
    run(session, client, live(cfg))
    second = client.box("sent")[1]
    assert second["threadId"] == row.gmail_thread_id and second["headers"]["In-Reply-To"] == row.rfc_message_id
    assert second["subject"] == "Re: Hello Acme" and c.state == "sent"


def test_silence_two_weeks_after_follow_up_closes_the_company(session, cfg):
    client = gmail.MockGmail()
    c, d, row = sent(session, cfg, client)
    (f,) = sender.prepare_followups(session, cfg, db.now() + timedelta(days=10))
    outreach.approve(session, f)
    run(session, client, live(cfg))
    sender.sync_replies(session, client, cfg, now=db.now() + timedelta(days=3))
    assert c.state == "sent"
    sender.sync_replies(session, client, cfg, now=db.now() + timedelta(days=20))
    assert c.state == "closed"


def test_background_pass_sends_scheduled_drafts_but_never_plain_approved_ones(session, cfg):
    approved(session, name="Plain", email="p@plain.com")
    _, timed = approved(session, name="Timed", email="t@timed.com")
    sender.schedule(session, timed, db.now() - timedelta(minutes=1))
    client = gmail.MockGmail()
    run(session, client, live(cfg), only_scheduled=True)
    assert [m["to"] for m in client.box("sent")] == ["t@timed.com"]


def test_sends_are_paced_by_the_minimum_gap(session, cfg):
    for n in ("A", "B"):
        approved(session, name=n, email=f"x@{n.lower()}.com")
    waits = []
    sender.send_approved(session, gmail.MockGmail(), live(cfg, min_seconds_between_sends=90), limit=10, sleep=waits.append)
    assert len(waits) == 1 and 80 < waits[0] <= 90


def test_resume_is_attached_and_a_changed_file_blocks_the_send(session, cfg, tmp_path):
    resume = tmp_path / "Ziheng_Wang_Resume.pdf"
    resume.write_bytes(b"%PDF-1.4 v1")
    c = live(cfg, attachment=str(resume))
    _, d = approved(session)
    d.approved_hash = outreach.content_hash(d.subject, d.body, "dana@acme.com", str(resume))
    session.commit()
    client = gmail.MockGmail()
    run(session, client, c)
    assert client.box("sent")[0]["attachments"] == ["Ziheng_Wang_Resume.pdf"]

    _, d2 = approved(session, name="Beta", email="lee@beta.com")
    d2.approved_hash = outreach.content_hash(d2.subject, d2.body, "lee@beta.com", str(resume))
    session.commit()
    resume.write_bytes(b"%PDF-1.4 v2 edited after approval")
    assert "content changed since approval" in run(session, client, c)[0][1]
    _, d3 = approved(session, name="Gamma", email="kim@gamma.com")
    d3.approved_hash = outreach.content_hash(d3.subject, d3.body, "kim@gamma.com", str(resume))
    session.commit()
    resume.unlink()
    assert "not found" in run(session, client, c)[0][1]
