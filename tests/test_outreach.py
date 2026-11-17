import pytest

from companyscout import db, llm, outreach
from companyscout.schemas import CandidateProfile, Project

GOOD = ("Hi Dana, I read that Ledgerline added invoice reading for accounting firms, and the part about checking "
        "extracted totals stood out to me. I built FinDash, a dashboard that reads invoices with the Claude API and "
        "checks every number the model returns against its own arithmetic before a business owner confirms it. "
        "That validation work is close to what your document pipeline has to get right, and I would like to help "
        "test and harden it. I study computer science at the University of Michigan. Would you be open to a short "
        "conversation about a Summer 2027 engineering internship at Ledgerline? Thank you for reading, Larry Wang")


def profile():
    return CandidateProfile(name="Larry", projects=[Project(name="FinDash", summary="AI finance dashboard",
                            technologies=["Claude API"], tags=["fintech"], evidence=["sorts 14 cost lines into 4 groups"])],
                            source_text="FinDash sorts 14 cost lines into 4 groups. Claude API.")


def setup(session, status="verified_public", body=GOOD, **draft_kw):
    c = db.Company(name="Ledgerline", domain="ledgerline.example", state="shortlisted", shortlisted=True,
                   description="Invoice processing", research={"product": "Invoice processing since 2014"}, primary_project="FinDash")
    k = db.Contact(company=c, name="Dana Reyes", title="CTO", email="dana@ledgerline.example", email_status=status)
    session.add_all([c, k])
    session.commit()
    d = db.Draft(company_id=c.id, contact_id=k.id, subject="FinDash and Ledgerline", body=body, **draft_kw)
    session.add(d)
    session.commit()
    return c, d


def issues(session, cfg, d):
    return " | ".join(outreach.qc(session, None, cfg, d, profile())["issues"])


def test_well_formed_draft_passes_deterministic_qc(session, cfg):
    _, d = setup(session)
    assert outreach.qc(session, None, cfg, d, profile())["passed"] is True and d.status == "reviewed"


def test_sixty_word_draft_fails_qc(session, cfg):
    _, d = setup(session, body=" ".join(GOOD.split()[:60]) + " Summer 2027 at Ledgerline?")
    assert "words" in issues(session, cfg, d) and d.status == "drafted"


def test_number_not_found_in_resume_or_research_fails_qc(session, cfg):
    _, d = setup(session, body=GOOD.replace("I built FinDash", "I built FinDash, used by 5000 firms"))
    assert "5000" in issues(session, cfg, d)


def test_near_duplicate_of_another_companys_draft_fails_qc(session, cfg):
    setup(session)
    other = db.Company(name="Otherco", domain="other.example", state="shortlisted")
    session.add(other)
    session.commit()
    d = db.Draft(company_id=other.id, subject="s", body=GOOD.replace("Ledgerline", "Otherco"))
    session.add(d)
    session.commit()
    assert "similar" in issues(session, cfg, d)


def test_editing_an_approved_draft_clears_its_approval(session, cfg):
    c, d = setup(session)
    outreach.qc(session, None, cfg, d, profile())
    outreach.approve(session, d)
    assert d.status == "approved" and d.approved_hash and c.state == "approved"
    outreach.edit(session, d, body=GOOD + " PS: one more thing.")
    assert (d.status, d.approved_hash, c.state) == ("drafted", None, "drafted")


def test_approve_refuses_contact_without_verified_email(session, cfg):
    _, d = setup(session, status="inferred")
    outreach.qc(session, None, cfg, d, profile())
    with pytest.raises(outreach.ApprovalError, match="verified"):
        outreach.approve(session, d)


def test_approve_refuses_draft_that_failed_qc_unless_forced(session, cfg):
    _, d = setup(session, body="Too short. Summer 2027 at Ledgerline?")
    outreach.qc(session, None, cfg, d, profile())
    with pytest.raises(outreach.ApprovalError, match="quality"):
        outreach.approve(session, d)
    outreach.approve(session, d, force=True)
    assert d.status == "approved"


def test_generate_creates_reviewed_draft_and_refuses_to_replace_an_approved_one(session, cfg):
    c, old = setup(session)
    session.delete(old)
    session.commit()
    replies = {"draft": dict(subjects=["a", "b", "c"], body=GOOD, rationale="why", followup="Hi Dana, following up on my note about Ledgerline invoices."),
               "qc": dict(verdict="pass", scores={"personalization": 4}, issues=[])}
    provider = llm.MockProvider(lambda task, prompt: replies[task])
    d = outreach.generate(session, provider, cfg, c, profile())
    assert (d.status, len(d.subjects), c.state) == ("reviewed", 3, "reviewed")
    outreach.approve(session, d)
    with pytest.raises(outreach.ApprovalError, match="approved"):
        outreach.generate(session, provider, cfg, c, profile())
    assert d.body == GOOD and d.status == "approved"


def test_llm_reviewer_rejection_fails_qc(session, cfg):
    _, d = setup(session)
    provider = llm.MockProvider(lambda task, prompt: dict(verdict="reject", scores={}, issues=["Claims a Rust project the resume lacks"]))
    assert "Rust" in " | ".join(outreach.qc(session, provider, cfg, d, profile())["issues"])


def test_qc_accepts_the_names_a_person_would_actually_write():
    from companyscout.outreach import name_variants
    assert {"Kit", "ConvertKit"} <= name_variants("Kit (ConvertKit)")
    assert "Plus One" in name_variants("Plus One Robotics")
    assert name_variants("8th Light") == {"8th Light"}
    import re
    assert not any(re.search(rf"\b{re.escape(n)}\b", "a toolkit for teams", re.I) for n in name_variants("Kit (ConvertKit)"))


def test_llm_reviewer_is_told_who_the_recipient_is(session, cfg):
    _, d = setup(session)
    prompts = []
    provider = llm.MockProvider(lambda task, prompt: prompts.append(prompt) or dict(verdict="pass", scores={}, issues=[]))
    outreach.qc(session, provider, cfg, d, profile())
    assert "RECIPIENT (from our contact research): Dana Reyes, CTO" in prompts[0]
