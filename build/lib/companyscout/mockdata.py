"""Offline fixtures for the `mock` provider (OUTREACH_PROVIDER=mock). All companies are fictional `.example` domains."""
import re

COMPANIES = {  # name: (domain, industry, description, employees, location, tags)
    "Ledgerline": ("ledgerline.example", "fintech", "Invoice and document processing software for regional accounting firms.",
                   95, "Ann Arbor, MI", ["fintech", "document-processing", "data-validation", "llm"]),
    "Graspworks": ("graspworks.example", "robotics", "Vision-guided pick-and-place cells for small manufacturers.",
                   140, "Pittsburgh, PA", ["robotics", "computer-vision", "automation"]),
    "Carebridge Systems": ("carebridge.example", "healthcare technology", "Patient intake software for outpatient clinics.",
                           220, "Remote", ["healthcare", "enterprise-saas"]),
}

BODY = ("Hi Dana, I read that {name} is building software where extracted data has to be right before a customer acts on "
        "it, and that is the part of the problem I find most interesting. I built FinDash, a dashboard that reads invoices "
        "with the Claude API and checks each number the model returns against its own arithmetic before the owner confirms "
        "it. I would like to help test and harden that kind of pipeline on your team. I study computer science at the "
        "University of Michigan. Would you be open to a short conversation about a Summer 2027 engineering internship at "
        "{name}? Thank you for reading, Larry Wang")


def default_handler(task, prompt):
    if task == "discover":
        return {"companies": [dict(name=n, website=f"https://{d}", industry=i, description=desc, employee_estimate=e,
                                   employee_source=f"https://{d}/about", location=loc, source_url=f"https://{d}/about")
                              for n, (d, i, desc, e, loc, _) in COMPANIES.items()]}
    if task == "research":
        name = next((n for n in COMPANIES if n in prompt), "Ledgerline")
        d, industry, desc, employees, loc, tags = COMPANIES[name]
        return dict(product=desc, customers=[], industry=industry, employee_count=employees,
                    employee_source=f"https://{d}/about", founded_year=2014, location=loc, remote=loc == "Remote",
                    stability="Moderate Evidence", stability_confidence=0.7, stability_evidence=["Operating since 2014"],
                    stack=["Python", "JavaScript", "Node.js"], tags=tags, engineering_openings=["Software Engineer"],
                    contact=dict(name="Dana Reyes", title="CTO", email=None, profile_url=f"https://{d}/team"),
                    sources=[f"https://{d}/about", f"https://{d}/careers"])
    if task == "profile":
        return dict(name="Test Candidate", headline="CS student", education="University of Michigan", projects=[
            dict(name="FinDash", summary="AI-assisted finance dashboard.", technologies=["JavaScript", "Node.js", "Claude API"],
                 tags=["fintech", "data-validation", "llm", "document-processing"], evidence=["Reads invoices and checks each number."]),
            dict(name="declutter", summary="iOS app with a YOLO11 vision backend.", technologies=["Swift", "PyTorch", "YOLO11", "FastAPI"],
                 tags=["computer-vision", "ios", "ml"], evidence=["Fine-tuned YOLO11-seg."]),
            dict(name="Michigan Dining", kind="experience", summary="Student worker.")])
    if task == "news":
        return {"items": []}
    if task == "opening":
        return dict(affects_hiring=False, relevance="Related to document processing.", suggested_opening="I saw your recent launch.")
    if task == "draft":
        name = re.search(r"^Company: (.+)$", prompt, re.M).group(1)
        return dict(subjects=[f"Invoice validation work and {name}", f"Summer 2027 internship at {name}", "A student project close to your product"],
                    body=BODY.format(name=name), rationale="Matched on data validation.",
                    followup=f"Hi Dana, a quick follow-up on my note about {name}. Happy to share a short demo if useful.")
    if task == "qc":
        return dict(verdict="pass", scores={"personalization": 4, "technical_accuracy": 4}, issues=[])
    raise KeyError(f"mock provider has no fixture for task {task!r}")
