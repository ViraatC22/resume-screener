"""Blind screening guardrails.

Referenced from crew.jsonc as task guardrails, so they run in code between the
agents. The screener never sees who a candidate is:

- `anonymize_candidates` (parse task) checks every readable resume was parsed
  exactly once, skips unreadable files and older versions of a resume (same
  email), saves each candidate's name and contact details to candidates.json
  under an ID (C1, C2, ...), and hands the screener the profiles with those
  details removed.
- `check_screening` (screen task) checks every candidate got a 1-10 score,
  sends the screener back if its reasoning mentions protected characteristics,
  applies the shortlist cutoff, books interview slots, and builds the report
  with names added back.

A guardrail returns (True, new_output) to replace the task's output, or
(False, feedback) to make the agent redo the task with that feedback.
"""

import json
import re
from datetime import datetime, timedelta
from pathlib import Path

from tools.resume_reader_tool import RESUMES_DIR, read_resume, resume_files

ROOT = Path(__file__).resolve().parent.parent
CANDIDATES_FILE = ROOT / "candidates.json"

SHORTLIST_SCORE = 8
FIRST_INTERVIEW_HOUR = 10  # local time; later interviews follow hourly

CONTACT_FIELDS = {"name", "file", "email", "phone"}
FIELD_RE = re.compile(r"^\s*[-*•]?\s*\**\s*([A-Za-z ]+?)\s*\**\s*:\s*\**\s*(.*?)\s*\**\s*$")
HEADING_RE = re.compile(r"^\s*#{2,4}\s+(.+?)\s*$")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE_RE = re.compile(r"(?:\+\d{1,2}[\s.-]?)?(?:\(\d{3}\)|\d{3})[\s.-]?\d{3}[\s.-]\d{4}\b")
URL_RE = re.compile(r"\b(?:https?://|www\.)\S+|\b(?:github|linkedin|gitlab)\.com/\S+", re.I)

PROTECTED_RE = re.compile(
    r"\b(gender|male|female|man|woman|men|women|age|aged|years old|young|younger|youthful|elderly|"
    r"race|racial|ethnic\w*|nationality|national origin|foreigner|immigra\w*|"
    r"native speaker|accent|religio\w*|married|marital|pregnan\w*|maternity|"
    r"disabilit\w*|disabled)\b",
    re.I,
)
PRONOUN_RE = re.compile(r"\b(he|she|him|her|his|hers|himself|herself)\b", re.I)


def anonymize_candidates(output):
    """Guardrail for parse_resumes_task."""
    texts, skipped = {}, {}
    for path in resume_files():
        try:
            text = read_resume(path)
        except Exception:
            text = ""
        if text:
            texts[path.name] = text
        else:
            skipped[path.name] = "no readable text (possibly a scanned image)"

    entries, seen = {}, []
    for heading, lines in _blocks(output.raw):
        fields = {}
        kept = []
        for line in lines:
            match = FIELD_RE.match(line)
            key = match.group(1).strip().lower() if match else None
            if key in CONTACT_FIELDS:
                fields[key] = match.group(2).strip()
            elif line.strip():
                kept.append(line)

        if "file" not in fields and "email" not in fields:
            continue  # a section heading, not a candidate

        file_name = _match_file(fields.get("file", ""), texts)
        name = fields.get("name") or re.sub(r"^(\d+[.)]|candidate|name)\s*:?\s*", "", heading, flags=re.I).strip("*: ")
        if not file_name and _match_file(fields.get("file", ""), skipped):
            continue  # an unreadable file the parser listed anyway
        if not file_name:
            return (False, f"The block for '{heading}' needs a '- File:' line with the exact resume file name.")
        if file_name in seen:
            return (False, f"{file_name} appears more than once. Include every resume exactly once.")
        if not name or name.lower() == "not listed":
            return (False, f"The block for {file_name} is missing the candidate's name.")
        seen.append(file_name)

        email = _verified_email(fields.get("email", ""), texts[file_name])
        if email is None:
            return (
                False,
                f"The email you listed for {file_name} ({fields.get('email')}) is not in that "
                "resume. Copy it exactly as written.",
            )

        entries[file_name] = {
            "name": name, "email": email, "phone": fields.get("phone", ""), "file": file_name,
            "profile": _redact("\n".join(kept), name),
        }

    missing = [name for name in texts if name not in seen]
    if missing:
        return (False, f"These resumes are missing from your answer: {', '.join(missing)}. Include every resume exactly once.")

    # Several versions of one person's resume share an email: keep the newest
    # file so nobody is screened or invited twice.
    newest = {}
    for file_name, entry in entries.items():
        if not entry["email"]:
            continue
        other = newest.get(entry["email"])
        if other is None or _mtime(file_name) > _mtime(other):
            newest[entry["email"]] = file_name
    for file_name, entry in entries.items():
        keep = newest.get(entry["email"], file_name)
        if keep != file_name:
            skipped[file_name] = f"older version of {keep}"

    candidates, profiles = {}, {}
    for number, file_name in enumerate((f for f in texts if f not in skipped), start=1):
        cid = f"C{number}"
        # The anonymous profile stays in candidates.json so evaluate.py can
        # check the screener's reasoning against exactly what it was shown.
        profiles[cid] = entries[file_name]["profile"]
        candidates[cid] = entries[file_name]

    CANDIDATES_FILE.write_text(json.dumps({"candidates": candidates, "skipped": skipped}, indent=2))
    body = "\n\n".join(f"### {cid}\n{profiles[cid]}" for cid in sorted(profiles, key=_id_number))
    return (
        True,
        "Anonymous candidate profiles. Names, contact details, and file names were removed "
        "before screening. Refer to each candidate only by their ID.\n\n" + body,
    )


def check_screening(output):
    """Guardrail for screen_candidates_task."""
    data = json.loads(CANDIDATES_FILE.read_text())
    candidates = data["candidates"]

    evaluations = _evaluations(output.raw)
    missing = [cid for cid in candidates if cid not in evaluations]
    if missing:
        return (False, f"You did not score {', '.join(missing)}. Score every candidate ID exactly once as '### <ID>: <score>/10'.")
    for cid, (score, reasoning) in evaluations.items():
        if cid not in candidates:
            return (False, f"{cid} is not one of the candidate IDs. Valid IDs: {', '.join(candidates)}.")
        if score is None or not 1 <= score <= 10:
            return (False, f"Give {cid} a whole-number score from 1 to 10, written as '### {cid}: <score>/10'.")
        for pattern, problem in [(PROTECTED_RE, "mentions"), (PRONOUN_RE, "uses the pronoun")]:
            hit = pattern.search(reasoning)
            if hit:
                return (
                    False,
                    f"Your evaluation of {cid} {problem} '{hit.group(0)}'. The profiles are anonymous: "
                    "refer to candidates by ID or as 'the candidate', and judge only skills, "
                    "experience, and results.",
                )

    role_match = re.search(r"candidate for the (.+?) role", output.description or "")
    role = role_match.group(1) if role_match else "the open role"
    ranked = sorted(evaluations, key=lambda cid: (-evaluations[cid][0], _id_number(cid)))
    shortlist = [cid for cid in ranked if evaluations[cid][0] >= SHORTLIST_SCORE]

    first_slot = _next_business_day().replace(hour=FIRST_INTERVIEW_HOUR, minute=0, second=0, microsecond=0)
    for cid in candidates:
        score = evaluations[cid][0]
        candidates[cid]["score"] = score
        candidates[cid]["decision"] = "INTERVIEW" if score >= SHORTLIST_SCORE else "REJECT"
        candidates[cid]["reasoning"] = evaluations[cid][1]
        candidates[cid].pop("interview_start", None)
    for slot, cid in enumerate(shortlist):
        candidates[cid]["interview_start"] = (first_slot + timedelta(hours=slot)).isoformat()
    CANDIDATES_FILE.write_text(json.dumps({**data, "role": role, "candidates": candidates}, indent=2))

    return (True, _report(role, candidates, evaluations, ranked, shortlist, data.get("skipped", {})))


def _blocks(text):
    """Split Markdown into (heading, lines) pairs at ##/### headings."""
    blocks = []
    for line in text.replace("```markdown", "").replace("```", "").splitlines():
        heading = HEADING_RE.match(line)
        if heading:
            blocks.append((heading.group(1).strip("*: "), []))
        elif blocks:
            blocks[-1][1].append(line)
    return blocks


def _match_file(value, file_names):
    value = value.strip().strip("`'\"").lower()
    for name in file_names:
        if value in (name.lower(), Path(name).stem.lower()):
            return name
    return None


def _mtime(file_name):
    return (RESUMES_DIR / file_name).stat().st_mtime


def _verified_email(value, resume_text):
    """The parsed email if it's really in the resume, else the resume's only email."""
    found = [e.lower() for e in EMAIL_RE.findall(resume_text)]
    value = value.strip().strip("<>`").lower()
    if value in found:
        return value
    if len(set(found)) == 1:
        return found[0]
    return "" if not found else None


def _redact(text, name):
    text = EMAIL_RE.sub("[email removed]", text)
    text = URL_RE.sub("[link removed]", text)
    text = PHONE_RE.sub("[phone removed]", text)
    # Unicode letters, so names like "José Núñez" are removed too.
    for token in sorted(set(re.findall(r"[^\W\d_][\w'-]*", name)), key=len, reverse=True):
        if len(token) > 1:
            text = re.sub(rf"\b{re.escape(token)}\b", "[name removed]", text, flags=re.I)
    return text.strip()


def _evaluations(text):
    """Map candidate ID -> (score, reasoning) from '### C1: 7/10' style entries."""
    evaluations, current = {}, None
    for line in text.splitlines():
        head = re.match(r"^\s*(#{1,4}\s*|\*\*)?\s*(?:candidate\s+)?(C\d+)\b(.*)$", line, re.I)
        cid = head.group(2).upper() if head and (head.group(1) or "/10" in line) else None
        if cid and cid != current:
            current = cid if cid not in evaluations else None  # ignore repeats, e.g. a summary
            if current:
                evaluations[cid] = [_score(head.group(3)), []]
        elif not cid and re.match(r"^\s*#{1,2}\s", line):
            current = None
        elif current:
            if evaluations[current][0] is None and _score(line) is not None:
                evaluations[current][0] = _score(line)
                continue
            evaluations[current][1].append(line)
    return {cid: (score, "\n".join(lines).strip()) for cid, (score, lines) in evaluations.items()}


def _score(text):
    match = re.search(r"(\d{1,2}(?:\.\d+)?)\s*/\s*10", text)
    return int(float(match.group(1)) + 0.5) if match else None


def _next_business_day():
    day = datetime.now().astimezone() + timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def _id_number(cid):
    return int(cid[1:])


def _report(role, candidates, evaluations, ranked, shortlist, skipped):
    lines = [
        f"# Candidate Screening: {role}",
        "",
        "Screening was blind: the screener saw anonymous profiles with names, contact details, "
        "and file names removed in code. Names were added back after scoring.",
        "",
        "## Ranking",
        "",
        "| Rank | ID | Candidate | Score | Decision |",
        "|---|---|---|---|---|",
    ]
    for rank, cid in enumerate(ranked, start=1):
        c = candidates[cid]
        lines.append(f"| {rank} | {cid} | {c['name']} | {c['score']}/10 | {c['decision']} |")

    lines += ["", "## Shortlist", ""]
    for cid in shortlist:
        start = datetime.fromisoformat(candidates[cid]["interview_start"]).astimezone()
        lines.append(f"- {cid} ({candidates[cid]['name']}): interview {start:%a %b %d, %H:%M %Z}")
    if not shortlist:
        lines.append("None")

    lines += ["", "## Evaluations", ""]
    for cid in sorted(evaluations, key=_id_number):
        c = candidates[cid]
        lines += [f"### {cid} ({c['name']}): {c['score']}/10, {c['decision']}", evaluations[cid][1], ""]

    if skipped:
        lines += ["## Skipped files", ""]
        lines += [f"- {name}: {reason}" for name, reason in skipped.items()]
        lines.append("")

    lines += [
        "## Bias check",
        "",
        "Passed. No evaluation mentions gender, age, race, ethnicity, nationality, religion, "
        "family status, or disability, or uses gendered pronouns. The screener is sent back "
        "to redo its evaluations if any do.",
    ]
    return "\n".join(lines)
