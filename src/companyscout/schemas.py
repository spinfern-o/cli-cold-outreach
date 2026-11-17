"""Pydantic contracts for everything an LLM or the web hands back. Anything that fails here is rejected."""
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

# Shared vocabulary so company research and candidate projects can be matched deterministically.
TAGS = ["llm", "ai-evaluation", "applied-ai", "ml", "computer-vision", "nlp", "fintech", "accounting",
        "data-validation", "analytics", "developer-tools", "infrastructure", "api-integration", "full-stack",
        "web", "mobile", "ios", "ecommerce", "search", "robotics", "automation", "manufacturing", "healthcare",
        "wellness", "education", "enterprise-saas", "consulting", "data-engineering", "security", "iot",
        "document-processing", "small-business", "logistics", "insurance", "legal", "real-estate", "energy"]
Stability = Literal["Strong Evidence", "Moderate Evidence", "Uncertain", "Elevated Risk"]
Category = Literal["product", "funding", "hiring", "acquisition", "leadership", "layoffs", "risk", "other"]


def _known_tags(v):
    return [t.lower().strip() for t in v if t.lower().strip() in TAGS]


class DiscoveredCompany(BaseModel):
    name: str = Field(min_length=1)
    website: str = Field(min_length=4)
    industry: str = ""
    description: str = ""
    employee_estimate: Optional[int] = None
    employee_source: str = ""
    location: str = ""
    source_url: str = Field(min_length=8)  # where the company was found; no source, no record


class DiscoveryResult(BaseModel):
    companies: list[DiscoveredCompany]


class ContactOut(BaseModel):
    name: str = ""
    title: str = ""
    email: Optional[str] = None
    email_source_url: Optional[str] = None
    profile_url: Optional[str] = None


class CompanyResearch(BaseModel):
    product: str = Field(min_length=10)
    customers: list[str] = []
    industry: str = ""
    employee_count: Optional[int] = Field(default=None, ge=1)
    employee_source: str = ""
    founded_year: Optional[int] = Field(default=None, ge=1800, le=2100)
    location: str = ""
    remote: Optional[bool] = None
    stability: Stability
    stability_confidence: float = Field(ge=0, le=1)
    stability_evidence: list[str] = []
    stack: list[str] = []
    tags: list[str] = []
    engineering_openings: list[str] = []
    has_internship_evidence: bool = False  # explicit flag: the text field often says "none found"
    internship_evidence: str = ""
    engineering_presence: str = ""
    recent_announcements: list[str] = []
    risk_signals: list[str] = []
    feed_url: Optional[str] = None
    contact: Optional[ContactOut] = None
    sources: list[str] = Field(min_length=1)

    _tags = field_validator("tags")(_known_tags)


class NewsFinding(BaseModel):
    title: str = Field(min_length=5)
    url: str = Field(min_length=8)
    summary: str = ""
    category: Category = "other"
    significance: int = Field(default=30, ge=0, le=100)
    published: Optional[str] = None  # ISO date


class NewsResult(BaseModel):
    items: list[NewsFinding] = []


class Opening(BaseModel):
    affects_hiring: bool = False
    relevance: str = ""
    suggested_opening: str = ""
    project: str = ""  # candidate project this news makes most relevant


class Project(BaseModel):
    name: str
    kind: str = "project"
    summary: str = ""
    technologies: list[str] = []
    tags: list[str] = []
    evidence: list[str] = []  # resume bullets, close to verbatim

    _tags = field_validator("tags")(_known_tags)


class CandidateProfile(BaseModel):
    name: str
    headline: str = ""
    education: str = ""
    credentials: list[str] = []
    projects: list[Project]
    strengths: list[str] = []
    source_text: str = ""


class EmailDraft(BaseModel):
    subjects: list[str] = Field(min_length=3, max_length=3)
    body: str = Field(min_length=50)
    rationale: str = ""
    followup: str = Field(min_length=20)


class QCReview(BaseModel):
    verdict: Literal["pass", "reject"]
    scores: dict[str, int] = {}
    issues: list[str] = []
