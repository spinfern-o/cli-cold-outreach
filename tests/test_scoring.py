from companyscout import db, scoring


def company(**kw):
    base = dict(name="Co", domain="co.com", industry="enterprise software", employee_count=100,
                stability="Moderate Evidence", stability_confidence=0.6, hiring="unknown", interest=50,
                stack=[], tags=[], sources=[], risk_flags=[])
    base.update(kw)
    return db.Company(**base)


def test_established_100_person_company_outranks_unproven_10_person_startup(cfg):
    established = company(employee_count=100, stability="Strong Evidence", founded_year=2012)
    startup = company(employee_count=10, stability="Uncertain", founded_year=2025)
    assert scoring.score(established, cfg)[0] > scoring.score(startup, cfg)[0]


def test_companies_over_750_employees_are_excluded_by_default(cfg):
    assert scoring.qualify(company(employee_count=800), cfg)[0] is False
    assert scoring.qualify(company(employee_count=100), cfg)[0] is True


def test_elevated_risk_scores_below_strong_evidence(cfg):
    assert scoring.score(company(stability="Elevated Risk"), cfg)[0] < scoring.score(company(stability="Strong Evidence"), cfg)[0]


def test_score_reports_confidence_separately_from_attractiveness(cfg):
    sourced = company(employee_source="https://co.com/about", sources=["https://co.com", "https://x.com/co"], researched_at=db.now())
    score_a, _, conf_a = scoring.score(sourced, cfg)
    score_b, _, conf_b = scoring.score(company(), cfg)
    assert conf_a > conf_b and score_a == score_b


def test_filters_by_size_stability_and_industry(session):
    session.add_all([
        company(name="Small", domain="s.com", employee_count=40, industry="fintech", stability="Strong Evidence"),
        company(name="Mid", domain="m.com", employee_count=200, industry="healthcare technology", stability="Uncertain"),
        company(name="Big", domain="b.com", employee_count=600, industry="fintech", stability="Moderate Evidence"),
    ])
    session.commit()
    names = lambda **f: sorted(c.name for c in scoring.filter_companies(session, **f))
    assert names(size=(15, 300)) == ["Mid", "Small"]
    assert names(stable=True) == ["Big", "Small"]
    assert names(industry="fintech") == ["Big", "Small"]
