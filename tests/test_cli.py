import csv
import json

from typer.testing import CliRunner

from companyscout import db, gmail
from companyscout.cli import app

RESUME = ("Test Candidate. University of Michigan. FinDash: JavaScript, Node.js, Claude API — reads invoices and checks "
          "each number against its own arithmetic. declutter: Swift, PyTorch, YOLO11, FastAPI. Michigan Dining: student worker.")


def test_full_workflow_through_the_cli(home):
    runner = CliRunner()

    def run(*args, ok=True, input=None):
        result = runner.invoke(app, list(args), input=input)
        assert (result.exit_code == 0) is ok, f"{args}: {result.output}\n{result.exception!r}"
        return result.output

    resume = home / "resume.txt"
    resume.write_text(RESUME)
    run("init")
    assert "Michigan Dining" not in run("profile", "import", str(resume)) and "FinDash" in run("profile", "show")
    assert "3" in run("discover", "--limit", "3")
    run("research", "--all")
    listing = run("companies", "--size", "15:300", "--stable")
    assert "Ledgerline" in listing and "Graspworks" in listing
    assert "Graspworks" not in run("companies", "--industry", "fintech")
    run("shortlist", "add", "1")
    assert "Ledgerline" in run("shortlist") and "Graspworks" not in run("shortlist")
    assert "Dana Reyes" in run("company", "1")
    run("news", "refresh", "--shortlist")
    run("news", "--shortlist")
    run("memory", "search", "invoice processing")
    run("generate", "--shortlist")
    assert "reviewed" in run("drafts")
    assert "verified" in run("approve", "1", ok=False)            # no verified address yet
    run("contact", "verify", "1", "--email", "dana@ledgerline.example", "--yes")
    run("approve", "1")
    assert "draft-only" in run("send", "--approved", "--limit", "5")
    assert gmail.MockGmail().box("sent") == [] and len(gmail.MockGmail().box("draft")) == 1
    run("config", "live_sending", "true")
    run("pause")
    assert "paused" in run("send", "--approved", "--yes", ok=False)
    run("resume")
    assert "sent" in run("send", "--approved", "--limit", "5", "--yes")
    (message,) = gmail.MockGmail().box("sent")
    assert message["to"] == "dana@ledgerline.example"
    gmail.MockGmail().add_reply(message["threadId"], "dana@ledgerline.example", "Happy to talk, send me your availability.")
    assert "reply" in run("inbox") and "dana@ledgerline.example" in run("replies")
    assert "replied" in run("pipeline")
    assert "1" in run("stats")
    run("followups")
    run("export", str(home / "companies.csv"))
    rows = list(csv.DictReader((home / "companies.csv").open()))
    assert {r["name"] for r in rows} >= {"Ledgerline", "Graspworks"} and rows[0]["employee_source"]
    run("export", str(home / "companies.json"))
    assert len(json.loads((home / "companies.json").read_text())) == 3
    run("doctor")
    run("monitor", "status")
    s = db.connect()
    assert s.query(db.Company).filter_by(state="replied").one().name == "Ledgerline"
    s.close()


def test_review_lets_the_user_edit_then_requires_fresh_approval(home, monkeypatch):
    from companyscout import cli
    runner = CliRunner()
    resume = home / "resume.txt"
    resume.write_text(RESUME)
    for args in (["init"], ["profile", "import", str(resume)], ["discover", "--limit", "3"], ["research", "--all"],
                 ["shortlist", "add", "1"], ["contact", "verify", "1", "--email", "dana@ledgerline.example", "--yes"],
                 ["generate", "--shortlist"], ["approve", "1"]):
        assert runner.invoke(app, args).exit_code == 0, args
    monkeypatch.setattr(cli, "open_editor", lambda text, **kw: text.replace("Thank you for reading", "Thanks for your time"))
    out = runner.invoke(app, ["review", "--all"], input="e\nq\n").output
    assert "approval cleared" in out
    s = db.connect()
    d = s.query(db.Draft).one()
    assert d.approved_hash is None and "Thanks for your time" in d.body and d.status in ("drafted", "reviewed")
    s.close()


def test_gmail_draft_puts_company_name_in_to_when_no_verified_address(home):
    runner = CliRunner()
    resume = home / "resume.txt"
    resume.write_text(RESUME)
    pdf = home / "Resume.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    for args in (["init"], ["profile", "import", str(resume)], ["discover", "--limit", "3"], ["research", "--all"],
                 ["shortlist", "add", "1"], ["generate", "1"], ["config", "attachment", str(pdf)]):
        assert runner.invoke(app, args).exit_code == 0, args
    name = db.connect().get(db.Company, 1).name
    out = runner.invoke(app, ["gmail", "draft", "1"]).output
    assert f"To: {name}" in out
    drafts = gmail.MockGmail().box("draft")
    assert len(drafts) == 1 and drafts[0]["to"] == name and drafts[0]["attachments"] == ["Resume.pdf"]
    assert gmail.MockGmail().box("sent") == []
    assert "already in Gmail Drafts" in runner.invoke(app, ["gmail", "draft", "1"]).output
    assert "already in Gmail Drafts" in runner.invoke(app, ["review"], input="d\nq\n").output  # [d] in review = same command
