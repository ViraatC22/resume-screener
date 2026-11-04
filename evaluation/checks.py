"""Deterministic checks on the screening crew's decisions.

Each check compares what the agents produced (candidates.json) with the resumes
themselves or with fixed rules, so the same run always gets the same verdict:

- years of experience the parser or screener claimed vs. the dates on the resume
- skills and numbers the screener cites vs. what the resume actually says
- decisions vs. the 8/10 cutoff, blinding, bias terms, invites, coverage

A "fail" means the decision needs a person to look at it; a "warn" is worth
reading but doesn't block sign-off.
"""

import re
from datetime import date, datetime
from typing import NamedTuple

from tools.blind_screening import EMAIL_RE, PHONE_RE, PRONOUN_RE, PROTECTED_RE, SHORTLIST_SCORE
from tools.resume_reader_tool import read_resume, resume_files

YEARS_TOLERANCE = 1.0  # a claim more than this above the resume's dates is unsupported

MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
_DATE = r"(?:(?P<{p}m>{months})[a-z]*\.?\s+|(?P<{p}n>\d{{1,2}})/)?(?P<{p}y>(?:19|20)\d{{2}})"
DATE_RANGE_RE = re.compile(
    _DATE.format(p="s", months="|".join(MONTHS))
    + r"\s*(?:-|–|—|to)\s*(?:(?P<present>present|current|now)|"
    + _DATE.format(p="e", months="|".join(MONTHS)) + ")",
    re.I,
)
CLAIMED_YEARS_RE = re.compile(r"years of experience\W*\s*~?(\d+(?:\.\d+)?)", re.I)
REASONING_YEARS_RE = re.compile(
    r"(\d+(?:\.\d+)?)\+?\s*(?:years?|yrs?)\s+of\s+(?:[\w-]+\s+){0,3}experience", re.I
)
# Metrics such as "820ms", "31%", "$40M", "6x", "1,200+", "25min".
METRIC_RE = re.compile(r"(?<![\w.])[$~]?(\d+(?:\.\d+)?)\s?(%|ms|min|h|k|m|b|x|\+)(?![a-z])", re.I)

# Skills the screener might cite. Each maps to the spellings that count as the
# resume mentioning it.
SKILLS = {
    "Python": ["python"], "Django": ["django"], "FastAPI": ["fastapi"], "Flask": ["flask"],
    "Celery": ["celery"], "SQLAlchemy": ["sqlalchemy"], "pytest": ["pytest"],
    "PostgreSQL": ["postgresql", "postgres"], "MySQL": ["mysql"], "MongoDB": ["mongodb"],
    "Redis": ["redis"], "Kafka": ["kafka"], "RabbitMQ": ["rabbitmq"], "AWS": ["aws"],
    "GCP": ["gcp"], "Azure": ["azure"], "Docker": ["docker"], "Kubernetes": ["kubernetes", "k8s"],
    "Terraform": ["terraform"], "Java": ["java"], "JavaScript": ["javascript"],
    "TypeScript": ["typescript"], "Node.js": ["node\\.js", "nodejs"], "React": ["react"],
    "Jest": ["jest"], "GraphQL": ["graphql"], "Spark": ["spark"], "Hadoop": ["hadoop"],
    "Prometheus": ["prometheus"], "Grafana": ["grafana"], "asyncio": ["asyncio"],
    "Rust": ["rust"], "C++": ["c\\+\\+"], "Go": ["Go", "Golang"],  # Go: case-sensitive below
}


class Finding(NamedTuple):
    severity: str  # "fail" or "warn"
    check: str
    message: str


def check_run(data: dict) -> tuple[dict[str, list[Finding]], list[Finding]]:
    """Per-candidate findings, and findings about the run as a whole."""
    texts = {path.name: read_resume(path) for path in resume_files()}
    per_candidate = {cid: _check_candidate(c, texts.get(c["file"], "")) for cid, c in data["candidates"].items()}
    return per_candidate, _check_whole_run(data, texts)


def _check_candidate(c: dict, resume: str) -> list[Finding]:
    findings = []
    reasoning, profile = c.get("reasoning", ""), c.get("profile", "")
    if not reasoning:
        return [Finding("fail", "reasoning", "No reasoning was saved for this decision, so it can't be reviewed.")]

    # Decision follows the rule.
    expected = "INTERVIEW" if c["score"] >= SHORTLIST_SCORE else "REJECT"
    if c["decision"] != expected:
        findings.append(Finding("fail", "decision", f"Score {c['score']}/10 should be {expected}, but the decision is {c['decision']}."))

    # Years of experience: what the parser and screener claimed vs. the resume's dates.
    actual = resume_years(resume)
    claims = [("parser", m.group(1)) for m in CLAIMED_YEARS_RE.finditer(profile)]
    claims += [("screener", m.group(1)) for m in REASONING_YEARS_RE.finditer(reasoning)]
    for who, value in claims:
        claimed = float(value)
        if actual is None:
            if claimed > 0:
                findings.append(Finding("fail", "years", f"The {who} claims {value} years of experience, but the resume has no dated work history."))
        elif claimed > actual + YEARS_TOLERANCE:
            findings.append(Finding("fail", "years", f"The {who} claims {value} years of experience; the resume's dates add up to {actual:.1f}."))

    # Skills the screener credits the candidate with must appear in the resume.
    # "No evidence of Python" isn't a claim, so negated mentions don't count.
    for skill, spellings in SKILLS.items():
        flags = 0 if skill == "Go" else re.I
        skill_re = re.compile(rf"(?<![\w.+])(?:{'|'.join(spellings)})(?![\w+])", flags)
        if _credited(skill_re, reasoning) and not skill_re.search(resume):
            findings.append(Finding("fail", "unsupported skill", f"The reasoning cites {skill}, which isn't in the resume."))

    # Numbers the screener cites must appear in the resume (catches made-up or
    # miscalculated metrics, e.g. "a 90% improvement" the resume never states).
    resume_flat = resume.replace(",", "")
    for number, unit in set(METRIC_RE.findall(reasoning.replace(",", ""))):
        if not re.search(rf"(?<![\d.]){re.escape(number)}\s?{re.escape(unit)}", resume_flat, re.I):
            findings.append(Finding("fail", "unsupported number", f"The reasoning cites {number}{unit}, which isn't in the resume."))

    # Blind screening: the screener's input carried no identity.
    leaked = [m.group(0) for m in EMAIL_RE.finditer(profile)] + [m.group(0) for m in PHONE_RE.finditer(profile)]
    leaked += [token for token in re.findall(r"[^\W\d_][\w'-]*", c["name"]) if len(token) > 2
               and re.search(rf"\b{re.escape(token)}\b", profile, re.I)]
    if leaked:
        findings.append(Finding("fail", "blinding", f"The anonymous profile still contained: {', '.join(sorted(set(leaked)))}."))

    # Bias terms in the reasoning (the guardrail should already have caught these).
    for pattern in (PROTECTED_RE, PRONOUN_RE):
        hit = pattern.search(reasoning)
        if hit:
            findings.append(Finding("fail", "bias", f"The reasoning uses '{hit.group(0)}'."))

    # Invites only go to the shortlist.
    if c.get("invite_uid") and c["decision"] != "INTERVIEW":
        findings.append(Finding("fail", "invite", "A rejected candidate was sent an interview invite."))
    if c["decision"] == "INTERVIEW" and not c.get("invite_uid"):
        findings.append(Finding("warn", "invite", "Shortlisted, but no invite was sent."))
    return findings


def _check_whole_run(data: dict, texts: dict) -> list[Finding]:
    findings = []
    candidates = data["candidates"].values()
    accounted = {c["file"] for c in candidates} | set(data.get("skipped", {}))
    for name in texts:
        if name not in accounted:
            findings.append(Finding("fail", "coverage", f"{name} was neither screened nor listed as skipped."))
    for name, reason in data.get("skipped", {}).items():
        findings.append(Finding("warn", "coverage", f"{name} was skipped: {reason}. A person should check it."))

    starts = [c["interview_start"] for c in candidates if c.get("interview_start") and c.get("status") != "withdrawn"]
    if len(starts) != len(set(starts)):
        findings.append(Finding("fail", "schedule", "Two candidates are booked into the same interview slot."))
    for start in starts:
        slot = datetime.fromisoformat(start).astimezone()
        if slot.weekday() >= 5 or not 9 <= slot.hour <= 17:
            findings.append(Finding("fail", "schedule", f"An interview is booked outside working hours: {slot:%a %b %d %H:%M}."))
    return findings


NEGATION_RE = re.compile(r"\b(no|not|without|lacks?|lacking|never|missing|absent|none|nor|neither)\b|n't\b", re.I)


def _credited(pattern: re.Pattern, text: str) -> bool:
    """True if a mention isn't preceded by a negation in its own sentence."""
    for sentence in re.split(r"(?<=[.!?;])\s+", text):
        for match in pattern.finditer(sentence):
            if not NEGATION_RE.search(sentence[: match.start()]):
                return True
    return False


def resume_years(text: str) -> float | None:
    """Total years covered by the date ranges in a resume (overlaps counted once)."""
    today = date.today()
    spans = []
    for m in DATE_RANGE_RE.finditer(text):
        start = int(m["sy"]) * 12 + _month(m["sm"], m["sn"], default=1) - 1
        if m["present"]:
            end = today.year * 12 + today.month
        else:
            end = int(m["ey"]) * 12 + _month(m["em"], m["en"], default=12)
        if end > start:
            spans.append((start, end))
    if not spans:
        return None
    months, covered_to = 0, None
    for start, end in sorted(spans):
        if covered_to is not None and start < covered_to:
            start = covered_to
        if end > start:
            months += end - start
            covered_to = end
        covered_to = max(covered_to or end, end)
    return months / 12


def _month(name, number, default) -> int:
    if name:
        return MONTHS.index(name[:3].lower()) + 1
    if number and 1 <= int(number) <= 12:
        return int(number)
    return default
