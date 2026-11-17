"""Offline fixtures for the `mock` provider (OUTREACH_PROVIDER=mock). All companies are fictional `.example` domains."""

RESEARCH = dict(
    product="Invoice and document processing software for regional accounting firms.",
    customers=["Regional accounting firms"], industry="fintech", employee_count=95,
    employee_source="https://ledgerline.example/about", founded_year=2014, location="Ann Arbor, MI", remote=True,
    stability="Moderate Evidence", stability_confidence=0.7, stability_evidence=["Operating since 2014"],
    stack=["Python", "JavaScript", "Node.js"], tags=["fintech", "document-processing", "data-validation", "llm"],
    engineering_openings=["Software Engineer"], internship_evidence="", recent_announcements=[],
    risk_signals=[], feed_url=None,
    contact=dict(name="Dana Reyes", title="CTO", email=None, email_source_url=None, profile_url="https://ledgerline.example/team"),
    sources=["https://ledgerline.example/about", "https://ledgerline.example/careers"])


def default_handler(task, prompt):
    if task == "discover":
        return {"companies": [
            dict(name=n, website=f"https://{d}", industry=i, description=desc, employee_estimate=e,
                 employee_source=f"https://{d}/about", location=loc, source_url=f"https://{d}/about")
            for n, d, i, desc, e, loc in [
                ("Ledgerline", "ledgerline.example", "fintech", "Invoice processing for accounting firms", 95, "Ann Arbor, MI"),
                ("Graspworks", "graspworks.example", "robotics", "Vision-guided pick-and-place cells", 140, "Pittsburgh, PA"),
                ("Carebridge Systems", "carebridge.example", "healthcare technology", "Patient intake software", 220, "Remote"),
            ]]}
    if task == "research":
        return RESEARCH
    raise KeyError(f"mock provider has no fixture for task {task!r}")
