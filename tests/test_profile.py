from collections import Counter

from companyscout import db, profile
from companyscout.schemas import CandidateProfile, Project

TEXT = "FinDash: JavaScript, Node.js, Claude API. declutter: Swift, YOLO11, FastAPI, PyTorch. Colson Group: Java, OpenAI."


def raw_profile():
    return CandidateProfile(name="Larry", headline="CS student", projects=[
        Project(name="FinDash", summary="AI financial dashboard", technologies=["JavaScript", "Node.js", "Claude API", "Rust"],
                tags=["fintech", "data-validation", "full-stack", "llm"], evidence=["Shipped a P&L dashboard"]),
        Project(name="declutter", summary="iOS vision app", technologies=["Swift", "YOLO11", "FastAPI", "PyTorch"],
                tags=["computer-vision", "ios", "ml"], evidence=["Fine-tuned YOLO11-seg"]),
        Project(name="Colson Group / EBG", summary="LLM benchmark", technologies=["Java", "OpenAI"],
                tags=["llm", "ai-evaluation", "api-integration"], evidence=["Automate 30-prompt benchmarks"]),
        Project(name="Michigan Dining", summary="Dining hall job", technologies=[], tags=[], evidence=[]),
    ])


def test_clean_drops_excluded_experiences_and_unverified_technologies(cfg):
    p = profile.clean(raw_profile(), TEXT, cfg)
    assert "Michigan Dining" not in [x.name for x in p.projects]
    assert "Rust" not in p.projects[0].technologies and "JavaScript" in p.projects[0].technologies


def test_match_picks_vision_project_for_vision_company_and_findash_for_fintech(cfg):
    p = profile.clean(raw_profile(), TEXT, cfg)
    vision = db.Company(name="V", domain="v.com", tags=["computer-vision", "robotics"], stack=["Python", "PyTorch"])
    fintech = db.Company(name="F", domain="f.com", tags=["fintech", "data-validation"], stack=["Node.js"])
    assert profile.match(p, vision, Counter())[0] == "declutter"
    assert profile.match(p, fintech, Counter())[0] == "FinDash"


def test_match_does_not_keep_forcing_an_overused_project(cfg):
    p = profile.clean(raw_profile(), TEXT, cfg)
    llm_co = db.Company(name="L", domain="l.com", tags=["llm"], stack=[])
    first = profile.match(p, llm_co, Counter())[0]
    other = profile.match(p, llm_co, Counter({first: 9}))[0]
    assert {first, other} == {"FinDash", "Colson Group / EBG"}


def test_import_profile_stores_cleaned_knowledge_base(session, cfg):
    from companyscout import llm
    provider = llm.MockProvider(lambda task, prompt: raw_profile().model_dump())
    profile.import_profile(session, provider, cfg, TEXT)
    stored = profile.load_profile(session)
    assert [p.name for p in stored.projects] == ["FinDash", "declutter", "Colson Group / EBG"]


def test_clean_drops_dining_alias_and_entries_with_no_evidence(cfg):
    raw = raw_profile()
    raw.projects += [Project(name="MDining", summary="Listed only as a repository."),
                     Project(name="Empty Repo", summary="Listed only as a repository with an empty README.")]
    names = [p.name for p in profile.clean(raw, TEXT, cfg).projects]
    assert names == ["FinDash", "declutter", "Colson Group / EBG"]
